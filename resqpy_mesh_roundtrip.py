import numpy as np
# import requests
import asyncio

import resqpy.property as rqp
import resqpy.crs as rqc
import resqpy.model as rq
import resqpy.olio.uuid as bu
import resqpy.unstructured as rug
import resqpy.time_series as rts

# from etpclient_helper import openWebSocket, getDataspaces, deleteDataspace, addDataspace, putDataObject, putDataObjectArray, getResources, getDataObject, getDataArray
import map_api.utils
import map_api.etp_client

# ======================================================================
#  imports of xsdata definitions and defintions of XML parser functions
# ======================================================================
from xsdata.formats.dataclass.context import XmlContext
from xsdata.formats.dataclass.parsers import XmlParser
from xsdata.formats.dataclass.serializers import XmlSerializer
from xsdata.formats.dataclass.serializers.config import SerializerConfig
from resqml_objects import (
    EpcExternalPartReference,
    UnstructuredGridRepresentation,
    ObjUnstructuredGridRepresentation,
    LocalDepth3DCrs,
    Grid2DRepresentation,
    ContinuousProperty,
    DiscreteProperty,
    ObjDiscreteProperty
)

def xml_to_ug(xlms):
    with open("temp.xml", "w") as f:
        f.write(xlms)
    context = XmlContext()
    parser = XmlParser(context=context)
    ug = parser.parse(
        "temp.xml",
        UnstructuredGridRepresentation,
        # ObjUnstructuredGridRepresentation,
    )
    return ug

def xml_to_cp(xlms):
    with open("temp.xml", "w") as f:
        f.write(xlms)
    context = XmlContext()
    parser = XmlParser(context=context)
    cp = parser.parse( "temp.xml", ContinuousProperty)
    return cp

def xml_to_dp(xlms):
    with open("temp.xml", "w") as f:
        f.write(xlms)
    context = XmlContext()
    parser = XmlParser(context=context)
    # dp = parser.parse( "temp.xml", ObjDiscreteProperty)
    dp = parser.parse( "temp.xml", DiscreteProperty)
    return dp





# ===========================================================
# Read the input file (hexahedral mesh with properties)
# ===========================================================
# input_mesh_file = 'data/model_hexa_0.epc'
input_mesh_file = 'Q:/warmth/temp/model_hexa_0.epc'

model = rq.Model(input_mesh_file)
assert model is not None

#
# read mesh:  vertex positions and cell definitions
#
hexa_uuid = model.uuid(obj_type = 'UnstructuredGridRepresentation', title = "hexamesh")
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


mesh_uuid = hexa_uuid


import asyncio

import map_api.utils
import map_api.etp_client
input_mesh_file = 'Q:/warmth/temp/model_hexa_0.epc'

# =======================================
# Write the RESQML data to the ETP server
# =======================================

etp_host = "ws://localhost:9002"
dataspace = "pss_demo"

jwt_token = None

model = rq.Model(input_mesh_file)
assert model is not None

hexa_uuids = model.uuids(obj_type = 'UnstructuredGridRepresentation')
assert len(hexa_uuids)==1
hexa_uuid = hexa_uuids[0]
hexa = rug.HexaGrid(model, uuid = hexa_uuid)
assert hexa is not None

# property_titles = ['Temperature', 'Age', 'LayerID', 'Porosity_initial', 'Porosity_decay', 'Density_solid', 'insulance_thermal', 'Radiogenic_heat_production']
uuids = model.uuids(obj_type = 'ContinuousProperty')
prop_titles = [rqp.Property(model, uuid=u).title for u in uuids]
uuids = model.uuids(obj_type = 'DiscreteProperty')
prop_titles = prop_titles + [rqp.Property(model, uuid=u).title for u in uuids]

rddms_uris, prop_uris = asyncio.run(
    map_api.etp_client.upload_epc_mesh_to_rddms(input_mesh_file, hexa.title, prop_titles, 23031, etp_host, dataspace, "" )
)

# rddms_uris = ["eml:///dataspace('pss_demo')/eml20.EpcExternalPartReference(9ced6cb8-22ee-4e72-a880-c76513cf29ae)",
#  "eml:///dataspace('pss_demo')/resqml20.LocalDepth3dCrs(fe147149-1ef5-43fc-a07e-3883d4d33ce1)",
#  "eml:///dataspace('pss_demo')/resqml20.UnstructuredGridRepresentation(3ebf759b-bac1-11ee-a4b3-557654f85bb0)"]

# download_resqml_mesh(rddms_uris, etp_server_url, dataspace, authorization):
epc, crs, uns, points, nodes_per_face, nodes_per_face_cl, faces_per_cell, faces_per_cell_cl, cell_face_is_right_handed = \
    asyncio.run(
        map_api.etp_client.download_resqml_mesh(
            rddms_uris, etp_host, dataspace, ""
        )
    )


# jwt_token = "..."  # get token from azure CLI.  User must be part of the AAD group "rddms-users"
# args = {
#     'host': 'interop-rddms.azure-api.net',
#     'port': '443',
#     'token': jwt_token
# }

model_out = rq.new_model("returned-mesh-new.epc")
crs = rqc.Crs(model_out)
crs.create_xml()

# create an empty HexaGrid
hexa = rug.HexaGrid(model_out, title = uns.citation.title)
assert hexa.cell_shape == 'hexahedral'

hexa.crs_uuid = model_out.uuid(obj_type = 'LocalDepth3dCrs')
assert hexa.crs_uuid is not None

# cells
hexa.set_cell_count(uns.cell_count)

# faces
hexa.face_count = uns.geometry.face_count
hexa.faces_per_cell_cl = faces_per_cell_cl
hexa.faces_per_cell = faces_per_cell

# nodes
hexa.node_count = uns.geometry.node_count
hexa.nodes_per_face_cl = nodes_per_face_cl
hexa.nodes_per_face = nodes_per_face

# face handedness
hexa.cell_face_is_right_handed = cell_face_is_right_handed  # False for all faces for external cells

# points
hexa.points_cached = points

# basic validity check
hexa.check_hexahedral()

# write arrays, create xml and store model
hexa.write_hdf5()
hexa.create_xml()


for k in list(prop_uris.keys()):
    cp, pk, values = \
        asyncio.run(
            map_api.etp_client.download_resqml_mesh_property(
                prop_uris[k], epc, etp_host, dataspace, ""
            )
        )
    print(f"Property {k} values shape {values.shape} and mean {np.mean(values)}.  ")


# prop_name = 'Temperature'
for prop_name in prop_uris.keys():
    cp, pk, values = \
        asyncio.run(
            map_api.etp_client.download_resqml_mesh_property(
                prop_uris[prop_name], epc, etp_host, dataspace, ""
            )
        )
    is_continuous = type(cp)==map_api.resqml_objects.generated.ContinuousProperty
    if type(cp.property_kind)==map_api.resqml_objects.generated.LocalPropertyKind: 
        pk_title = cp.property_kind.local_property_kind.title 
    else:
        pk_title = cp.property_kind.kind.value

    _ = rqp.Property.from_array(model_out,
                                values,
                                source_info = cp.citation.originator,
                                keyword = prop_name,
                                support_uuid = hexa.uuid,
                                property_kind = pk_title,
                                indexable_element = cp.indexable_element.value,
                                uom = cp.uom if is_continuous else "Euc",
                                discrete = not is_continuous) 
 

model_out.store_epc()









# ==================================================================================================
#  In order to write the data arrays: read back the DataObjects and parse for "path in hdf5"
#    NOTE: this is unnecessary! we can read the path in hdf from the resqpy object
# ==================================================================================================

guid4 = str(hexa_uuid)  # Guid of target object
dot4 = 'resqml20.obj_UnstructuredGridRepresentation'   # data object type of target object

ug = None
gds = asyncio.run( getDataspaces(wsm) )
for ii,ds in enumerate(gds):
    if (dataspace == ds.path):
        res0 = asyncio.run( getResources(wsm, ds.uri) )
        for res in res0:
            res1 = asyncio.run( getDataObject(wsm, res.uri ) )
            vv = list(res1.values())[0]
            if (guid4 in vv.resource.uri and 'UnstructuredGridRepresentation' in vv.resource.uri):
                object_xml_2 = vv.data.decode('utf-8')
                ug = xml_to_ug(object_xml_2)

assert ug is not None

cps = []  # continuous properties
dps = []  # discrete properties

for ii,ds in enumerate(gds):
    if (dataspace == ds.path):
        res0 = asyncio.run( getResources(wsm, ds.uri) )
        for res in res0:
            res1 = asyncio.run( getDataObject(wsm, res.uri ) )
            vv = list(res1.values())[0]
            if ('ContinuousProperty' in vv.resource.uri):
                cp = xml_to_cp(vv.data.decode('utf-8'))
                if cp.supporting_representation.uuid==str(hexa_uuid):
                    cps.append(cp)
            if ('DiscreteProperty' in vv.resource.uri):
                dp = xml_to_dp(vv.data.decode('utf-8'))
                if dp.supporting_representation.uuid==str(hexa_uuid):
                    dps.append(dp)




#
# create a etpproto-specific dict for the "put data object array" call
#
url_ExternalPartReference = f'eml:///dataspace(\'{dataspace}\')/eml20.EpcExternalPartReference({str(hexa.uuid)})'

#
# out mesh is defined by six arrays:
#
put_array_dict = {'dataArrays':
                  {
                      '0': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.points.coordinates.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list(hexa.points_cached.shape),
                              'data': {
                                  'item': {
                                      'values': hexa.points_cached.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                      '1': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.nodes_per_face.elements.values.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list( hexa.nodes_per_face.shape),
                              'data': {
                                  'item': {
                                      'values':  hexa.nodes_per_face.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                      '2': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.nodes_per_face.cumulative_length.values.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list( hexa.nodes_per_face_cl.shape),
                              'data': {
                                  'item': {
                                      'values':  hexa.nodes_per_face_cl.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                      '3': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.faces_per_cell.elements.values.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list( hexa.faces_per_cell.shape),
                              'data': {
                                  'item': {
                                      'values':  hexa.faces_per_cell.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                      '4': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.faces_per_cell.cumulative_length.values.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list( hexa.faces_per_cell_cl.shape),
                              'data': {
                                  'item': {
                                      'values':  hexa.faces_per_cell_cl.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                      '5': {
                          'uid': {
                              'uri': url_ExternalPartReference,
                              'pathInResource': ug.geometry.cell_face_is_right_handed.values.path_in_hdf_file
                          },
                          'array': {
                              'dimensions': list( hexa.cell_face_is_right_handed.shape),
                              'data': {
                                  'item': {
                                      'values':  hexa.cell_face_is_right_handed.flatten().tolist()
                                  }
                              }
                          },
                          'customData': {}
                      },
                  }
                }


#
# add properties to put_array_dict
#  
prop_titles=['Temperature', 'Age', 'LayerID', 'Porosity_initial', 'Porosity_decay', 'Density_solid', 'insulance_thermal', 'Radiogenic_heat_production']

title="Age"
for title in prop_titles:
    prop_uuid = model.uuid(title = title)
    prop = rqp.Property(model, uuid = prop_uuid)
    print(title)
    try:
        cp_prop = [cp for cp in cps if cp.citation.title==title and cp.supporting_representation.uuid==str(hexa_uuid)][0]
    except IndexError:
        cp_prop = [dp for dp in dps if dp.citation.title==title and dp.supporting_representation.uuid==str(hexa_uuid)][0]
    # print(title, prop.indexable_element(), prop.uom(), prop.array_ref()[0:10], cp_prop )
    ind = len(put_array_dict['dataArrays'])
    pihf = cp_prop.patch_of_values[0].values.values.path_in_hdf_file   # assume only one patch_of_values
    dd = {
            'uid': {
                'uri': url_ExternalPartReference,
                'pathInResource': pihf
            },
            'array': {
                'dimensions': list(  prop.array_ref().shape ),
                'data': {
                    'item': {
                        'values':   prop.array_ref().flatten().tolist()
                    }
                }
            },
            'customData': {}
        }
    put_array_dict['dataArrays'][str(ind)] = dd

    
# put data object arrays using etpclient-python
#
pdoa = asyncio.run(
    putDataObjectArray(wsm, put_array_dict)
)





#=================================================================================================
# read data back in using etpclient-python
#=================================================================================================

# guid4 = str(mesh_uuid)  # Guid of target object
# dot4 = 'resqml20.obj_UnstructuredGridRepresentation'   # data object type of target object


# get UnstructuredGrid data object and the six mesh-related data arrays
#
gds = asyncio.run( getDataspaces(wsm) )
for ii,ds in enumerate(gds):
    if (dataspace == ds.path):
        res0 = asyncio.run( getResources(wsm, ds.uri) )
        for res in res0:
            res1 = asyncio.run( getDataObject(wsm, res.uri ) )
            vv = list(res1.values())[0]
            if (guid4 in vv.resource.uri and 'UnstructuredGridRepresentation' in vv.resource.uri):
                object_xml_2 = vv.data.decode('utf-8')
                ug = xml_to_ug(object_xml_2)    
                uri = url_ExternalPartReference
                points = asyncio.run( getDataArray(wsm, uri, ug.geometry.points.coordinates.path_in_hdf_file ) )
                npf = asyncio.run( getDataArray(wsm, uri, ug.geometry.nodes_per_face.elements.values.path_in_hdf_file ) )
                npf_cl = asyncio.run( getDataArray(wsm, uri, ug.geometry.nodes_per_face.cumulative_length.values.path_in_hdf_file ) )
                fpc = asyncio.run( getDataArray(wsm, uri, ug.geometry.faces_per_cell.elements.values.path_in_hdf_file ) )
                fpc_cl = asyncio.run( getDataArray(wsm, uri, ug.geometry.faces_per_cell.cumulative_length.values.path_in_hdf_file ) )
                cfrh = asyncio.run( getDataArray(wsm, uri, ug.geometry.cell_face_is_right_handed.values.path_in_hdf_file ) )

#
# get all properties that use our mesh as support and store in dict "props"
# NOTE: for now we fetch *every data object* in the dataspace, and filter by type and support. This is wasteful!
#
props = {}
gds = asyncio.run( getDataspaces(wsm) )
for ii,ds in enumerate(gds):
    if (dataspace == ds.path):
        res0 = asyncio.run( getResources(wsm, ds.uri) )
        for res in res0:
            res1 = asyncio.run( getDataObject(wsm, res.uri ) )
            vv = list(res1.values())[0]
            if ( 'ContinuousProperty' in vv.resource.uri):
                object_xml_2 = vv.data.decode('utf-8')
                cp = xml_to_cp(object_xml_2)   
                if cp.supporting_representation.uuid==str(hexa_uuid):
                    pihf = cp.patch_of_values[0].values.values.path_in_hdf_file 
                    dd = asyncio.run( getDataArray(wsm, url_ExternalPartReference, pihf ) )
                    props[cp.citation.title] = {
                        'title': cp.citation.title,
                        'data': dd,
                        'indexable_element': cp.indexable_element.value,
                        'uom': cp.uom,
                        'is_integer': type(cp.patch_of_values[0].values)=="resqml_objects.generated.IntegerHdf5Array",
                    }
            if ( 'DiscreteProperty' in vv.resource.uri):
                object_xml_2 = vv.data.decode('utf-8')
                dp = xml_to_dp(object_xml_2)   
                if dp.supporting_representation.uuid==str(hexa_uuid):
                    pihf = dp.patch_of_values[0].values.values.path_in_hdf_file
                    dd = asyncio.run( getDataArray(wsm, url_ExternalPartReference, pihf ) )
                    props[dp.citation.title] = {
                        'title': dp.citation.title,
                        'data': dd,
                        'indexable_element': dp.indexable_element.value,
                        'uom': 'integer',
                        'is_integer': type(dp.patch_of_values[0].values)=="resqml_objects.generated.IntegerHdf5Array",
                    }






#===============================================================================
# write hexahedral mesh and properties out again, as fetched from ETP server
#===============================================================================

model_out = rq.new_model("returned-mesh.epc")
crs = rqc.Crs(model_out)
crs.create_xml()

# create an empty HexaGrid
hexa = rug.HexaGrid(model_out, title = "hexamesh")
assert hexa.cell_shape == 'hexahedral'

hexa.crs_uuid = model_out.uuid(obj_type = 'LocalDepth3dCrs')
assert hexa.crs_uuid is not None

# cells
hexa.set_cell_count(ug.cell_count)

# faces
hexa.face_count = ug.geometry.face_count
hexa.faces_per_cell_cl = fpc_cl
hexa.faces_per_cell = fpc

# nodes
hexa.node_count = ug.geometry.node_count
hexa.nodes_per_face_cl = npf_cl
hexa.nodes_per_face = npf

# face handedness
hexa.cell_face_is_right_handed = cfrh  # False for all faces for external cells

# points
hexa.points_cached = points

# basic validity check
hexa.check_hexahedral()

# write arrays, create xml and store model
hexa.write_hdf5()
hexa.create_xml()

# write known properties, if present in "props" dict

Temp_per_vertex = [p for p in props.values() if p['title']=='Temperature' and p['indexable_element']=='nodes']
if len(Temp_per_vertex)>0:
    _ = rqp.Property.from_array(model_out,
                                Temp_per_vertex[0]['data'],
                                source_info = 'roundtrip_tester',
                                keyword = 'Temperature',
                                support_uuid = hexa.uuid,
                                property_kind = 'thermodynamic temperature',
                                indexable_element = 'nodes',
                                uom = 'degC')

age_per_vertex = [p for p in props.values() if p['title']=='Age' and p['indexable_element']=='nodes']
if age_per_vertex is not None:
    _ = rqp.Property.from_array(model_out,
                                age_per_vertex[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'Age',
                                support_uuid = hexa.uuid,
                                property_kind = 'geological age',
                                indexable_element = 'nodes',
                                uom = 'y')

lid_per_cell = [p for p in props.values() if p['title']=='LayerID' and p['indexable_element']=='cells']
if lid_per_cell is not None:
    _ = rqp.Property.from_array(model_out,
                                lid_per_cell[0]['data'].astype(np.int32),
                                source_info = 'SubsHeat',
                                keyword = 'LayerID',
                                support_uuid = hexa.uuid,
                                property_kind = 'layer ID',
                                indexable_element = 'cells',
                                uom = 'Euc',
                                discrete=True)
        
poro0_per_cell = [p for p in props.values() if p['title']=='Porosity_initial' and p['indexable_element']=='cells']
if poro0_per_cell is not None:
    _ = rqp.Property.from_array(model_out,
                                poro0_per_cell[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'Porosity_initial',
                                support_uuid = hexa.uuid,
                                property_kind = 'porosity',
                                indexable_element = 'cells',
                                uom = 'm3/m3')

decay_per_cell = [p for p in props.values() if p['title']=='Porosity_decay' and p['indexable_element']=='cells']
if decay_per_cell is not None:
    _ = rqp.Property.from_array(model_out,
                                decay_per_cell[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'Porosity_decay',
                                support_uuid = hexa.uuid,
                                property_kind = 'porosity decay',
                                indexable_element = 'cells',
                                uom = 'Euc')

density_per_cell = [p for p in props.values() if p['title']=='Density_solid' and p['indexable_element']=='cells']
if density_per_cell is not None:
    _ = rqp.Property.from_array(model_out,
                                density_per_cell[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'Density_solid',
                                support_uuid = hexa.uuid,
                                property_kind = 'density',
                                indexable_element = 'cells',
                                uom = 'kg/m3')
insulance_per_cell = [p for p in props.values() if p['title']=='insulance_thermal' and p['indexable_element']=='cells']
if insulance_per_cell is not None:
    #
    # we write thermal conductivity as its inverse, the thermal insulance
    #
    _ = rqp.Property.from_array(model_out,
                                insulance_per_cell[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'insulance_thermal',
                                support_uuid = hexa.uuid,
                                property_kind = 'thermal insulance',
                                indexable_element = 'cells',
                                uom = 'deltaK.m2/W')

rhp_per_cell = [p for p in props.values() if p['title']=='Radiogenic_heat_production' and p['indexable_element']=='cells']
if rhp_per_cell is not None:
    _ = rqp.Property.from_array(model_out,
                                rhp_per_cell[0]['data'],
                                source_info = 'SubsHeat',
                                keyword = 'Radiogenic_heat_production',
                                support_uuid = hexa.uuid,
                                property_kind = 'heat',
                                indexable_element = 'cells',
                                uom = 'W/m3')

model_out.store_epc()



