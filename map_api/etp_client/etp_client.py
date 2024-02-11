import datetime
import math
import json
import tempfile
import zipfile
import os
import uuid
import re

from . import etp_helper
import map_api.resqml_objects as resqml_objects

import websockets
import lxml.etree as ET
import h5py
import numpy as np

# SerializerError: LocalDepth3dCrs is not derived from DataObjectReference
from xsdata.models.datatype import XmlDateTime
from xsdata.formats.dataclass.context import XmlContext
from xsdata.formats.dataclass.parsers import XmlParser
from xsdata.formats.dataclass.serializers import XmlSerializer
from xsdata.formats.dataclass.serializers.config import SerializerConfig


# TODO: Is there a limit from pss-data-gateway?
# The websockets-library seems to have a limit that corresponds to the one from
# the ETP server.
MAX_WEBSOCKET_MESSAGE_SIZE = int(1.6e7)  # From the published ETP server


# TODO: Check pathing when the api is called
# with open("package.json", "r") as f:
#     jschema = json.load(f)
APPLICATION_NAME = "geomint"
APPLICATION_VERSION = "0.0.1"


async def upload_resqml_objects(
    ws, msg_id, max_payload_size, dataspace, resqml_objects
):
    serializer = XmlSerializer(config=SerializerConfig())

    dataspaces = []
    data_object_types = []
    uuids = []
    xmls = []
    for obj in resqml_objects:
        dataspaces.append(dataspace)
        data_object_types.append(get_data_object_type(obj))
        uuids.append(obj.uuid)
        xmls.append(str.encode(serializer.render(obj)))

    # FIXME: Handle possible errors from the ETP-server.
    records = await etp_helper.put_data_objects(
        ws, msg_id, dataspaces, data_object_types, uuids, xmls
    )
    assert all(key == "success" for record in records for key in record)

    rddms_uris = [
        etp_helper.get_data_object_uri(dataspace, dot, _uuid)
        for dataspace, dot, _uuid in zip(dataspaces, data_object_types, uuids)
    ]
    return rddms_uris


async def delete_resqml_objects(etp_server_url, rddms_uris, authorization):
    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )
        # TODO: Use the max_payload_size to ensure that data is uploaded in
        # chunks when needed.
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]

        records = await etp_helper.delete_data_objects(ws, msg_id, rddms_uris)
        assert len(records[0]["deletedUris"]) == len(rddms_uris)

        # Close session.
        await etp_helper.close_session(ws, msg_id, "Done deleting resqml objects")

    return records[0]["deletedUris"]


async def upload_array(
    ws, msg_id, max_payload_size, dataspace, epc, path_in_resource, array
):
    # This function uploads a single array that might potentially be oversized.

    # NOTE: For now the array should be in float32 as the ETP server does not
    # yet seem to support float64. The values will at least be converted to
    # float32, so take care when values are returned in case float64 is
    # uploaded.
    arr_size = array.size * array.dtype.itemsize

    # Check if we can upload the full array in one go.
    if arr_size < max_payload_size:
        # Upload the full array.

        # FIXME: Handle errors.
        records = await etp_helper.put_data_arrays(
            ws,
            msg_id,
            [dataspace],
            [get_data_object_type(epc)],
            [epc.uuid],
            [path_in_resource],
            [array],
        )
        # Verify that the upload was a success.
        assert len(records) == 1
        assert list(records[0])[0] == "success"

        return

    # Upload the array in several subarrays due to it being oversized.
    records = await etp_helper.put_uninitialized_data_arrays(
        ws,
        msg_id,
        dataspace,
        get_data_object_type(epc),
        epc.uuid,
        [path_in_resource],
        [array.shape],
    )
    # Verify that the upload was a success
    assert len(records) == 1
    assert len(list(records[0])) == 1
    assert list(records[0])[0] == "success"

    # Split matrix on axis 0, i.e., preserve the remaining shapes for each
    # block.
    blocks = np.array_split(
        array,
        int(np.ceil(arr_size / max_payload_size)),
    )

    # Upload each block separately.
    starts = [0] * len(array.shape)
    for block in blocks:
        records = await etp_helper.put_data_subarrays(
            ws,
            msg_id,
            dataspace,
            get_data_object_type(epc),
            epc.uuid,
            [path_in_resource],
            [block],
            [starts],
        )
        # Verify that the upload was a success
        assert len(records) == 1
        assert list(records[0])[0] == "success"

        # Increment row index to the next block.
        starts[0] += block.shape[0]


async def upload_resqml_surface(
    epc, crs, gri, surf_array, etp_server_url, dataspace, authorization
):
    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )

        # FIXME: Handle potential errors in records.
        # TODO: Use the max_payload_size to ensure that data is uploaded in
        # chunks or subarrays when needed.
        # TODO: Check if we need to include the message header size and the
        # metadata in the message body. That is, the max payload of uploaded data would then be:
        #   max_size = max_payload_size - sizeof(metadata)
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]

        # An alternate route is to first query for the dataspace and only
        # create it if it exists. However, we save a call to the server by just
        # trying to put the dataspace and ignore the error if it already
        # exists.
        records = await etp_helper.put_dataspaces(ws, msg_id, [dataspace])
        # The put_dataspaces returns a list of records, one for each dataspace.
        # However, as we are only adding a single dataspace there should only
        # be a single record.
        assert len(records) == 1
        # FIXME: We are here following the happy-path and assume that the only
        # "error message" we can get is if the dataspace exists. An alternative
        # would be to create more proper exception handling for the errors.

        rddms_urls = await upload_resqml_objects(
            ws, msg_id, max_payload_size, dataspace, [epc, crs, gri]
        )

        records = await upload_array(
            ws,
            msg_id,
            max_payload_size,
            dataspace,
            epc,
            gri.grid2d_patch.geometry.points.zvalues.values.path_in_hdf_file,
            surf_array,
        )

        await etp_helper.close_session(
            ws, msg_id, "Done uploading from upload_resqml_surface"
        )

    # Return the uri's of the three uploaded objects.
    return rddms_urls


async def upload_resqml_mesh_property(
    prop, cprop0, propkind, epc, etp_server_url, dataspace, authorization
):
    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]
        # records = await etp_helper.put_dataspaces(ws, msg_id, [dataspace])
        # assert len(records) == 1

        rddms_urls = await upload_resqml_objects(
            ws, msg_id, max_payload_size, dataspace, [propkind, cprop0]
        )

        records = await upload_array(
            ws,
            msg_id,
            max_payload_size,
            dataspace,
            epc,
            cprop0.patch_of_values[0].values.values.path_in_hdf_file,
            prop.array_ref(),
        )
        # print("=== records from upload array ===", records)

        await etp_helper.close_session(
            ws, msg_id, f"Done uploading from upload_resqml_mesh_property {cprop0.citation.title}"
        )

    # Return the uri's of the three uploaded objects.
    return rddms_urls


async def upload_resqml_mesh(
    uns, crs, epc, hexa, etp_server_url, dataspace, authorization
):
    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]

        records = await etp_helper.put_dataspaces(ws, msg_id, [dataspace])
        assert len(records) == 1

        rddms_urls = await upload_resqml_objects(
            ws, msg_id, max_payload_size, dataspace, [epc, crs, uns]
        )

        # points = hexa.points_cached,
        # nodes_per_face = hexa.nodes_per_face,
        # faces_per_cell = hexa.faces_per_cell,
        # cell_face_is_right_handed = hexa.cell_face_is_right_handed,
        # cumulative_length = hexa.nodes_per_face_cl,
        # faces_per_cell_cl = hexa.faces_per_cell_cl,

        # uns.geometry.points.coordinates.path_in_hdf_file
        #  uns.geometry.nodes_per_face.elements.values.path_in_hdf_file
        # uns.geometry.nodes_per_face.cumulative_length.values.path_in_hdf_file
        # uns.geometry.cell_face_is_right_handed.values.path_in_hdf_file

        records = await upload_array(
            ws,
            msg_id,
            max_payload_size,
            dataspace,
            epc,
            uns.geometry.points.coordinates.path_in_hdf_file,
            hexa.points_cached,
        )
        # print("=== records from upload array ===", records)

        records = await upload_array(
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.nodes_per_face.elements.values.path_in_hdf_file,
            hexa.nodes_per_face,
        )
        records = await upload_array(
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.nodes_per_face.cumulative_length.values.path_in_hdf_file,
            hexa.nodes_per_face_cl,
        )
        records = await upload_array(
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.faces_per_cell.elements.values.path_in_hdf_file,
            hexa.faces_per_cell,
        )
        records = await upload_array(
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.faces_per_cell.cumulative_length.values.path_in_hdf_file,
            hexa.faces_per_cell_cl,
        )
        records = await upload_array(
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.cell_face_is_right_handed.values.path_in_hdf_file,
            hexa.cell_face_is_right_handed,
        )

        await etp_helper.close_session(
            ws, msg_id, "Done uploading from upload_resqml_mesh"
        )

    # Return the uri's of the three uploaded objects.
    return rddms_urls


async def download_array(
    ws, msg_id, max_payload_size, dataspace, epc, path_in_resource
):
    # This function downloads a single array from the ETP-server connected to
    # the EpcExternalPartReference-object passed in with "path_in_resource" as
    # the key in hdf5-file. The point of this function is to use subarrays if
    # the request array is too large for a single websocket message.

    records = await etp_helper.get_data_array_metadata(
        ws,
        msg_id,
        dataspace,
        get_data_object_type(epc),
        epc.uuid,
        [path_in_resource],
    )
    assert len(records) == 1
    assert len(list(records[0]["arrayMetadata"])) == 1

    metadata = records[0]["arrayMetadata"].popitem()[1]
    arr_shape = metadata["dimensions"]
    # NOTE: We can in theory also retrieve the data type of the array, however,
    # the ETP-server seems for now to only support float32. Therefore, all data
    # will be interpreted as float32. That is, each element in the array will
    # be 4 bytes long.
    arr_size = math.prod(arr_shape) * 4

    # Check if we can retrieve the full array in one go.
    if arr_size < max_payload_size:
        # The array is small enough for a single request, so fetch everything
        # at once.
        records = await etp_helper.get_data_arrays(
            ws,
            msg_id,
            dataspace,
            get_data_object_type(epc),
            epc.uuid,
            [path_in_resource],
        )
        # Verify that we only got a single record with a single data array
        # entry.
        assert len(records) == 1
        assert len(list(records[0]["dataArrays"])) == 1

        return etp_helper.etp_data_array_to_numpy(records[0]["dataArrays"].popitem()[1])

    # We need to fetch the full array using several subarray-calls.
    num_blocks = int(np.ceil(arr_size / max_payload_size))
    # Split on axis 0, assuming that an element in axis 0 is not too large.
    # This code is based on the NumPy array_split-function here:
    # https://github.com/numpy/numpy/blob/d35cd07ea997f033b2d89d349734c61f5de54b0d/numpy/lib/shape_base.py#L731-L784
    # NOTE: If the array that is being fetched is multi-dimensional and we need
    # to split on several axes, then this code needs to generalized.
    num_each_section, extras = divmod(arr_shape[0], num_blocks)
    section_sizes = (
        [0]
        + extras * [num_each_section + 1]
        + (num_blocks - extras) * [num_each_section]
    )
    div_points = np.array(section_sizes, dtype=int).cumsum()

    # Create lists of starting indices and number of elements in each subarray.
    starts_list = []
    counts_list = []
    for i in range(num_blocks):
        starts_list.append([div_points[i], 0])
        counts_list.append(
            [div_points[i + 1] - div_points[i], arr_shape[1]],
        )

    blocks = []
    for starts, counts in zip(starts_list, counts_list):
        records = await etp_helper.get_data_subarrays(
            ws,
            msg_id,
            dataspace,
            get_data_object_type(epc),
            epc.uuid,
            [
                path_in_resource,
            ],
            [starts],
            [counts],
        )
        # Verify that we get a single record with a single subarray.
        assert len(records) == 1
        assert len(list(records[0]["dataSubarrays"])) == 1
        blocks.append(
            etp_helper.etp_data_array_to_numpy(records[0]["dataSubarrays"].popitem()[1])
        )

    # Check that we have received the requested number of blocks.
    assert len(blocks) == num_blocks

    # Assemble and return the full array.
    return np.concatenate(blocks, axis=0)



async def download_resqml_mesh(rddms_uris, etp_server_url, dataspace, authorization):
    # NOTE: This assumes that a "resqml-surface" consists of a
    # Grid2dRepresentation, an EpcExternalPartReference, and a LocalDepth3dCrs
    assert len(rddms_uris) == 3

    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )

        # TODO: Use the max_payload_size to ensure that data is downloaded in
        # chunks when needed.
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]

        # Download the grid objects.
        records = await etp_helper.get_data_objects(ws, msg_id, rddms_uris)
        # NOTE: Here we assume that all three objects fit in a single record
        data_objects = records[0]["dataObjects"]
        # NOTE: This test will not work in case of too large objects as the
        # records will be chunks. If so these should be assembled before being
        # returned here.

        assert len(data_objects) == len(rddms_uris)

        returned_resqml = read_returned_resqml_objects(data_objects)

        # NOTE: In case there are multiple objects of a single type (not in
        # this call, but in other functions) we can replace "next" by "list"
        # (or just keep the generator from "filter" as-is) to sort out all the
        # relevant objects.
        epc = next(
            filter(
                lambda x: isinstance(x, resqml_objects.EpcExternalPartReference),
                returned_resqml,
            )
        )
        crs = next(
            filter(
                lambda x: isinstance(x, resqml_objects.LocalDepth3dCrs),
                returned_resqml,
            )
        )
        uns = next(
            filter(
                lambda x: isinstance(x, resqml_objects.UnstructuredGridRepresentation),
                returned_resqml,
            )
        )

        points = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.points.coordinates.path_in_hdf_file,
        )

        nodes_per_face = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.nodes_per_face.elements.values.path_in_hdf_file,
        )
        nodes_per_face_cl = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.nodes_per_face.cumulative_length.values.path_in_hdf_file,
        )
        faces_per_cell = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.faces_per_cell.elements.values.path_in_hdf_file,
        )
        faces_per_cell_cl = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.faces_per_cell.cumulative_length.values.path_in_hdf_file,
        )
        cell_face_is_right_handed = await download_array( 
            ws, msg_id, max_payload_size, dataspace, epc,
            uns.geometry.cell_face_is_right_handed.values.path_in_hdf_file,
        )

        # Close session.
        await etp_helper.close_session(ws, msg_id, "Done downloading mesh")

    # Return xml's and array
    return epc, crs, uns, points, nodes_per_face, nodes_per_face_cl, faces_per_cell, faces_per_cell_cl, cell_face_is_right_handed


async def download_resqml_surface(rddms_uris, etp_server_url, dataspace, authorization):
    # NOTE: This assumes that a "resqml-surface" consists of a
    # Grid2dRepresentation, an EpcExternalPartReference, and a LocalDepth3dCrs
    assert len(rddms_uris) == 3

    headers = {"Authorization": authorization}

    async with websockets.connect(
        etp_server_url,
        extra_headers=headers,
        subprotocols=["etp12.energistics.org"],
        max_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    ) as ws:
        msg_id = etp_helper.ClientMessageId()
        records = await etp_helper.request_session(
            ws,
            msg_id,
            max_payload_size=MAX_WEBSOCKET_MESSAGE_SIZE,
            application_name=APPLICATION_NAME,
            application_version=APPLICATION_VERSION,
        )

        # TODO: Use the max_payload_size to ensure that data is downloaded in
        # chunks when needed.
        max_payload_size = records[0]["endpointCapabilities"][
            "MaxWebSocketMessagePayloadSize"
        ]["item"]

        # Download the grid objects.
        records = await etp_helper.get_data_objects(ws, msg_id, rddms_uris)
        # NOTE: Here we assume that all three objects fit in a single record
        data_objects = records[0]["dataObjects"]
        # NOTE: This test will not work in case of too large objects as the
        # records will be chunks. If so these should be assembled before being
        # returned here.

        assert len(data_objects) == len(rddms_uris)

        returned_resqml = read_returned_resqml_objects(data_objects)

        # NOTE: In case there are multiple objects of a single type (not in
        # this call, but in other functions) we can replace "next" by "list"
        # (or just keep the generator from "filter" as-is) to sort out all the
        # relevant objects.
        epc = next(
            filter(
                lambda x: isinstance(x, resqml_objects.EpcExternalPartReference),
                returned_resqml,
            )
        )
        crs = next(
            filter(
                lambda x: isinstance(x, resqml_objects.LocalDepth3dCrs),
                returned_resqml,
            )
        )
        gri = next(
            filter(
                lambda x: isinstance(x, resqml_objects.Grid2dRepresentation),
                returned_resqml,
            )
        )

        gri_array = await download_array(
            ws,
            msg_id,
            max_payload_size,
            dataspace,
            epc,
            gri.grid2d_patch.geometry.points.zvalues.values.path_in_hdf_file,
        )

        # Close session.
        await etp_helper.close_session(ws, msg_id, "Done downloading surface array")

    # Return xml's and array
    return epc, crs, gri, gri_array


async def upload_xtgeo_surface_to_rddms(
    surface, title, projected_epsg, etp_server_url, dataspace, authorization
):
    # ETP_SERVER_URL="ws://localhost:9002"
    # DATASPACE = "pss_demo"
    # epc, crs, gri, surf_array = convert_xtgeo_surface_to_resqml_grid(
    #     surface, "test2", "31031"
    # )    
    epc, crs, gri, surf_array = convert_xtgeo_surface_to_resqml_grid(
        surface, title, projected_epsg
    )
    return await upload_resqml_surface(
        epc, crs, gri, surf_array, etp_server_url, dataspace, authorization
    )

async def upload_epc_mesh_to_rddms(
    epc_filename, title_in, projected_epsg, etp_server_url, dataspace, authorization
):
    # uns, crs, epc = map_api.etp_client.convert_resqpy_mesh_to_resqml_mesh(None,"a",None)
    uns, crs, epc, hexa = convert_resqpy_mesh_to_resqml_mesh(epc_filename, title_in, projected_epsg)
    uns_rddms_uris = await upload_resqml_mesh(
        uns, crs, epc, hexa, etp_server_url, dataspace, authorization
    )
    cprop0, prop, propertykind0 = convert_resqpy_mesh_property_to_resqml_mesh(epc_filename, hexa, "Age", uns, epc )

    prop_rddms_uris = await upload_resqml_mesh_property(
        prop, cprop0, propertykind0, epc, etp_server_url, dataspace, authorization
    )
    print("===== prop_rddms_uris  ", prop_rddms_uris)

    return uns_rddms_uris
    


def get_data_object_type(obj):
    # This assumes that obj is instantiated.
    # Using auto-generated objects from xsdata with the object names preserved
    # from the schema-definition files we know that the class name is the same
    # as the RESQML-object name.
    return obj.__class__.__name__


def convert_resqpy_mesh_to_resqml_mesh(epc_filename, title_in, projected_epsg):
    import resqpy.property as rqp
    import resqpy.crs as rqc
    import resqpy.model as rq
    import resqpy.olio.uuid as bu
    import resqpy.unstructured as rug
    import resqpy.time_series as rts
    import resqpy.crs as rcrs    
    import numpy as np
    
    common_citation_fields = dict(
        creation=XmlDateTime.from_string(
            datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
        ),
        originator=APPLICATION_NAME,
        format=f"equinor:{APPLICATION_NAME}:v{APPLICATION_VERSION}",
    )
    schema_version = "2.0"

    model = rq.Model(epc_filename)
    assert model is not None

    #
    # read mesh:  vertex positions and cell definitions
    #
    hexa_uuid = model.uuid(obj_type = 'UnstructuredGridRepresentation', title = title_in )
    assert hexa_uuid is not None
    hexa = rug.HexaGrid(model, uuid = hexa_uuid)
    assert hexa is not None
    print(hexa.title, hexa.node_count, hexa.cell_count, hexa.cell_shape)
    assert hexa.cell_shape == 'hexahedral'

    print( hexa.points_ref().shape )   # numpy array of vertex positions
    cells = np.array( [ hexa.distinct_node_indices_for_cell(i) for i in range(hexa.cell_count) ]  ) # cell indices are read using this function(?)
    print( cells.shape )   # numpy array of vertex positions

    hexa.check_hexahedral()

    #
    # example read properties 
    #
    # titles=['Temperature', 'Age', 'LayerID', 'Porosity_initial', 'Porosity_decay', 'Density_solid', 'insulance_thermal', 'Radiogenic_heat_production']
    # for title in titles:
    #     prop_uuid = model.uuid(title = title)
    #     prop = rqp.Property(model, uuid = prop_uuid)
    #     print(title, prop.indexable_element(), prop.uom(), prop.array_ref()[0:10] )

    # mesh_uuid = hexa_uuid
    # crs_rp = rcrs.Crs(model, uuid = hexa.crs_uuid)

    # from lxml import etree    
    # crs_str = etree.tostring(crs_rp.create_xml(), pretty_print=False)

    # parser = XmlParser(context=XmlContext())

    # crs2 = parser.from_bytes(
    #     crs_str,
    #     resqml_objects.LocalDepth3dCrs,
    # )
    crs = resqml_objects.LocalDepth3dCrs(
        citation=resqml_objects.Citation(
            title=f"CRS for {title_in}",
            **common_citation_fields,
        ),
        schema_version=schema_version,
        uuid=str(uuid.uuid4()),
        # NOTE: I assume that we let the CRS have no offset, and add any offset
        # in the grid instead.
        xoffset=0.0,
        yoffset=0.0,
        zoffset=0.0,
        areal_rotation=resqml_objects.PlaneAngleMeasure(
            # Here rotation should be zero!
            value=0.0,
            uom=resqml_objects.PlaneAngleUom.DEGA,
        ),
        # NOTE: Verify that this is the projected axis order
        projected_axis_order=resqml_objects.AxisOrder2d.EASTING_NORTHING,
        projected_uom=resqml_objects.LengthUom.M,
        vertical_uom=resqml_objects.LengthUom.M,
        zincreasing_downward=True,
        vertical_crs=resqml_objects.VerticalUnknownCrs(
            unknown="unknown",
        ),
        projected_crs=resqml_objects.ProjectedCrsEpsgCode(
            epsg_code=projected_epsg,
        ),
    )


    epc = resqml_objects.EpcExternalPartReference(
        citation=resqml_objects.Citation(
            title="Hdf Proxy",
            **common_citation_fields,
        ),
        schema_version=schema_version,
        uuid=str(uuid.uuid4()),
        mime_type="application/x-hdf5",
    )
    
    geom = resqml_objects.UnstructuredGridGeometry(
        local_crs = resqml_objects.DataObjectReference(
                content_type=f"application/x-resqml+xml;version={schema_version};type={get_data_object_type(crs)}",
                title=crs.citation.title,
                uuid=crs.uuid,
            ),
        node_count=hexa.node_count,
        face_count=hexa.face_count,
        cell_shape = hexa.cell_shape,        
        # points = hexa.points_cached,
        points = resqml_objects.Point3dHdf5Array(
            coordinates = resqml_objects.Hdf5Dataset(
                path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/points",
                hdf_proxy=resqml_objects.DataObjectReference(
                    content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                    title=epc.citation.title,
                    uuid=str(epc.uuid),
                ),                
            )
        ),
        nodes_per_face = resqml_objects.ResqmlJaggedArray(
            elements = resqml_objects.IntegerHdf5Array(
                null_value = -1,
                values = resqml_objects.Hdf5Dataset(
                    path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/nodes_per_face",
                    hdf_proxy=resqml_objects.DataObjectReference(
                        content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                        title=epc.citation.title,
                        uuid=str(epc.uuid),
                    ),                
                )
            ),
            cumulative_length = resqml_objects.IntegerHdf5Array(
                null_value = -1,
                values = resqml_objects.Hdf5Dataset(
                    path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/nodes_per_face_cl",
                    hdf_proxy=resqml_objects.DataObjectReference(
                        content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                        title=epc.citation.title,
                        uuid=str(epc.uuid),
                    ),                
                )
            ),
        ),
        faces_per_cell = resqml_objects.ResqmlJaggedArray(
            elements = resqml_objects.IntegerHdf5Array(
                null_value = -1,
                values = resqml_objects.Hdf5Dataset(
                    path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/faces_per_cell",
                    hdf_proxy=resqml_objects.DataObjectReference(
                        content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                        title=epc.citation.title,
                        uuid=str(epc.uuid),
                    ),                
                )
            ),
            cumulative_length = resqml_objects.IntegerHdf5Array(
                null_value = -1,
                values = resqml_objects.Hdf5Dataset(
                    path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/faces_per_cell_cl",
                    hdf_proxy=resqml_objects.DataObjectReference(
                        content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                        title=epc.citation.title,
                        uuid=str(epc.uuid),
                    ),                
                )
            ),
        ),
        cell_face_is_right_handed = resqml_objects.BooleanHdf5Array(
            values = resqml_objects.Hdf5Dataset(
                path_in_hdf_file=f"/RESQML/{str(hexa_uuid)}/cell_face_is_right_handed",
                hdf_proxy=resqml_objects.DataObjectReference(
                    content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                    title=epc.citation.title,
                    uuid=str(epc.uuid),
                ),                
            )
        )
    )

    #
    uns = resqml_objects.UnstructuredGridRepresentation(
        uuid=str(hexa.uuid),
        schema_version=schema_version,
        # surface_role=resqml_objects.SurfaceRole.MAP,
        citation=resqml_objects.Citation(
                title=hexa.title,
                **common_citation_fields,
            ),
        cell_count = hexa.cell_count,
        geometry = geom,
    )

    return uns, crs, epc, hexa

def convert_resqpy_mesh_property_to_resqml_mesh(epc_filename, hexa, prop_title, uns, epc ):
    import resqpy.property as rqp 
    import numpy as np
    
    common_citation_fields = dict(
        creation=XmlDateTime.from_string(
            datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
        ),
        originator=APPLICATION_NAME,
        format=f"equinor:{APPLICATION_NAME}:v{APPLICATION_VERSION}",
    )
    schema_version = "2.0"

    model = rq.Model(epc_filename)
    assert model is not None

    #
    # example read properties 
    #
    # titles=['Temperature', 'Age', 'LayerID', 'Porosity_initial', 'Porosity_decay', 'Density_solid', 'insulance_thermal', 'Radiogenic_heat_production']
    # for title in titles:
    #     prop_uuid = model.uuid(title = title)
    #     prop = rqp.Property(model, uuid = prop_uuid)
    #     print(title, prop.indexable_element(), prop.uom(), prop.array_ref()[0:10] )

    prop_uuid = model.uuid(title = "Age")  # prop_title
    prop = rqp.Property(model, uuid = prop_uuid)
    pk = rqp.PropertyKind( model, uuid = prop.local_property_kind_uuid() )

    propertykind0 = resqml_objects.PropertyKind(
        schema_version = schema_version,
        citation=resqml_objects.Citation(
                title=hexa.title,
                **common_citation_fields,
            ),        
        naming_system = "urn:resqml:bp.com:resqpy",
        is_abstract = False,
        representative_uom = "y",
        parent_property_kind = resqml_objects.StandardPropertyKind(
            kind = "continuous",
        ),
        uuid = str(pk.uuid),
    )

    pov = resqml_objects.PatchOfValues(
        # representation_patch_index = 0,
        values = resqml_objects.DoubleHdf5Array(
            values = resqml_objects.Hdf5Dataset(
                path_in_hdf_file=f"/RESQML/{str(prop_uuid)}/values",
                hdf_proxy=resqml_objects.DataObjectReference(
                    content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                    title=epc.citation.title,
                    uuid=str(epc.uuid),
                ),                
            )
        ),
    )

    cprop0 = resqml_objects.ContinuousProperty(
        schema_version = schema_version,
        citation=resqml_objects.Citation(
                title=prop_title,
                **common_citation_fields,
            ),       
        uuid = str(prop.uuid),
        uom = prop.uom(),
        count = 1,
        indexable_element = "nodes", # "cells"
        supporting_representation = resqml_objects.DataObjectReference(
                content_type=f"application/x-resqml+xml;version={schema_version};type={get_data_object_type(uns)}",
                title=uns.citation.title,
                uuid=uns.uuid,
            ),
        property_kind = resqml_objects.LocalPropertyKind(
            local_property_kind = resqml_objects.DataObjectReference(
                content_type=f"application/x-resqml+xml;version={schema_version};type={get_data_object_type(propertykind0)}",
                title=propertykind0.citation.title,
                uuid=propertykind0.uuid,
            ),
        ),
        minimum_value = prop.minimum_value(),
        maximum_value = prop.maximum_value(),
        facet = resqml_objects.PropertyKindFacet(
            facet = prop.facet_type(),
            value = prop.facet(),
        ),
        patch_of_values = [pov],     
    )
    
    return cprop0, prop, propertykind0



def convert_xtgeo_surface_to_resqml_grid(surf, title, projected_epsg):
    # Build the RESQML-objects "manually" from the generated dataclasses.
    # Their content is described also in the RESQML v2.0.1 standard that is
    # available for download here:
    # https://publications.opengroup.org/standards/energistics-standards/v231a
    common_citation_fields = dict(
        creation=XmlDateTime.from_string(
            datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
        ),
        originator=APPLICATION_NAME,
        format=f"equinor:{APPLICATION_NAME}:v{APPLICATION_VERSION}",
    )
    schema_version = "2.0"

    epc = resqml_objects.EpcExternalPartReference(
        citation=resqml_objects.Citation(
            title="Hdf Proxy",
            **common_citation_fields,
        ),
        schema_version=schema_version,
        uuid=str(uuid.uuid4()),
        mime_type="application/x-hdf5",
    )

    assert np.abs(surf.get_rotation()) < 1e-7

    crs = resqml_objects.LocalDepth3dCrs(
        citation=resqml_objects.Citation(
            title=f"CRS for {title}",
            **common_citation_fields,
        ),
        schema_version=schema_version,
        uuid=str(uuid.uuid4()),
        # NOTE: I assume that we let the CRS have no offset, and add any offset
        # in the grid instead.
        xoffset=0.0,
        yoffset=0.0,
        zoffset=0.0,
        areal_rotation=resqml_objects.PlaneAngleMeasure(
            # Here rotation should be zero!
            value=surf.get_rotation(),
            uom=resqml_objects.PlaneAngleUom.DEGA,
        ),
        # NOTE: Verify that this is the projected axis order
        projected_axis_order=resqml_objects.AxisOrder2d.EASTING_NORTHING,
        projected_uom=resqml_objects.LengthUom.M,
        vertical_uom=resqml_objects.LengthUom.M,
        zincreasing_downward=True,
        vertical_crs=resqml_objects.VerticalUnknownCrs(
            unknown="unknown",
        ),
        projected_crs=resqml_objects.ProjectedCrsEpsgCode(
            epsg_code=projected_epsg,
        ),
    )

    x0 = surf.xori
    y0 = surf.yori
    dx = surf.xinc
    dy = surf.yinc
    # NOTE: xtgeo uses nrow for axis 1 in the array, and ncol for axis 0.  This
    # means that surf.nrow is the fastest changing axis, and surf.ncol the
    # slowest changing axis, and we have surf.values.shape == (surf.ncol,
    # surf.nrow). The author of this note finds that confusing, but such is
    # life.
    nx = surf.ncol
    ny = surf.nrow

    gri = resqml_objects.Grid2dRepresentation(
        uuid=(grid_uuid := str(uuid.uuid4())),
        schema_version=schema_version,
        surface_role=resqml_objects.SurfaceRole.MAP,
        citation=resqml_objects.Citation(
            title=title,
            **common_citation_fields,
        ),
        grid2d_patch=resqml_objects.Grid2dPatch(
            # TODO: Perhaps we can use this for tiling?
            patch_index=0,
            # NumPy-arrays are C-ordered, meaning that the last index is
            # the index that changes most rapidly. However, xtgeo uses nrow for
            # axis 1 in the array, and ncol for axis 0. This means that
            # surf.nrow is the fastest changing axis, and surf.ncol the slowest
            # changing axis (as surf.values.shape == (surf.ncol, surf.nrow))
            fastest_axis_count=ny,
            slowest_axis_count=nx,
            geometry=resqml_objects.PointGeometry(
                local_crs=resqml_objects.DataObjectReference(
                    # NOTE: See Energistics Identifier Specification 4.0
                    # (it is downloaded alongside the RESQML v2.0.1
                    # standard) section 4.1 for an explanation on the
                    # format of content_type.
                    content_type=f"application/x-resqml+xml;version={schema_version};type={get_data_object_type(crs)}",
                    title=crs.citation.title,
                    uuid=crs.uuid,
                ),
                points=resqml_objects.Point3dZValueArray(
                    supporting_geometry=resqml_objects.Point3dLatticeArray(
                        origin=resqml_objects.Point3d(
                            coordinate1=x0,
                            coordinate2=y0,
                            coordinate3=0.0,
                        ),
                        # NOTE: The ordering in the offset-list should be
                        # preserved when the data is passed back and forth.
                        # However, _we_ need to ensure a consistent ordering
                        # for ourselves. In this setup I have set the slowest
                        # axis to come first, i.e., the x-axis or axis 0 in
                        # NumPy. The reason is so that it corresponds with the
                        # origin above where "coordinate1" is set to be the
                        # x0-coordinate, and "coordinate2" the y0-coordinate.
                        # However, we can change this as we see fit.
                        offset=[
                            # Offset for x-direction, i.e., the slowest axis
                            resqml_objects.Point3dOffset(
                                offset=resqml_objects.Point3d(
                                    coordinate1=1.0,
                                    coordinate2=0.0,
                                    coordinate3=0.0,
                                ),
                                spacing=resqml_objects.DoubleConstantArray(
                                    value=dx,
                                    count=nx - 1,
                                ),
                            ),
                            # Offset for y-direction, i.e., the fastest axis
                            resqml_objects.Point3dOffset(
                                offset=resqml_objects.Point3d(
                                    coordinate1=0.0,
                                    coordinate2=1.0,
                                    coordinate3=0.0,
                                ),
                                spacing=resqml_objects.DoubleConstantArray(
                                    value=dy,
                                    count=ny - 1,
                                ),
                            ),
                        ],
                    ),
                    zvalues=resqml_objects.DoubleHdf5Array(
                        values=resqml_objects.Hdf5Dataset(
                            path_in_hdf_file=f"/RESQML/{grid_uuid}/zvalues",
                            hdf_proxy=resqml_objects.DataObjectReference(
                                content_type=f"application/x-eml+xml;version={schema_version};type={get_data_object_type(epc)}",
                                title=epc.citation.title,
                                uuid=epc.uuid,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    # Use nan as the mask-value in the surface array
    surf_array = surf.values.filled(np.nan)

    return epc, crs, gri, surf_array


def read_returned_resqml_objects(data_objects):
    # This function creates a list of resqml-objects from the returned xml from
    # the ETP-server. It dynamically finds the relevant resqml dataclass using
    # the object name found in the xml. Its intention is to be used after
    # calling the get_data_objects-protocol.

    # Set up an XML-parser from xsdata.
    parser = XmlParser(context=XmlContext())

    print("=========== read_returned_resqml_objects =======")
    for do in data_objects.values():
        print("=============", ET.fromstring(do["data"]).tag)
        print(do["data"])
        print("=======================================")
        print("")

    return [
        parser.from_bytes(
            data_object["data"],
            getattr(
                resqml_objects,
                ET.QName(ET.fromstring(data_object["data"]).tag).localname,
            ),
        )
        for data_object in data_objects.values()
    ]
