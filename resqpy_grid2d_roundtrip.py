import numpy as np
import requests
import asyncio
import time
import json
import argparse
import pprint

import resqpy.model as rq
import resqpy.crs as rcrs
import resqpy.surface as rs

from xtgeo.surface import RegularSurface

from etpclient_helper import openWebSocket, getDataspaces, deleteDataspace, addDataspace, putDataObject, putDataObjectArray, getResources, getDataObject, getDataArray



#
# Read the .gri file
# NOTE: mysurf.yflip=1 means that positive z is down.  Not sure if/how this is to be indicated in the resqpy model.
#
input_gri_file = 'data/0.gri'
mysurf = RegularSurface(input_gri_file)
nj,ni = mysurf.ncol, mysurf.nrow
origin = (mysurf.xori, mysurf.yori, 0.0)
di, dj = mysurf.xinc, mysurf.yinc
z = mysurf.values.data
#

# ni,nj = 10,5
# z = np.random.rand(nj,ni)

#
# Initialize a new resqpy model
#
model = rq.new_model("test1.epc", quiet=False)
crs = rcrs.Crs(model)
crs.create_xml()
#
# NOTE: we should probably set up the model CRS origin from the .gri file CRS
# CRS:   __init__(parent_model: Model, uuid: Optional[UUID] = None, x_offset: float = 0.0, y_offset: float = 0.0, z_offset: float = 0.0, rotation: float = 0.0, rotation_units: str = 'dega', xy_units: str = 'm', z_units: str = 'm', z_inc_down: bool = True, axis_order: str = 'easting northing', time_units: Optional[str] = None, epsg_code: Optional[str] = None, title: Optional[str] = None, originator: Optional[str] = None, extra_metadata: Optional[Dict[str, str]] = None)

#
# create a regular mesh representation from our .gri file data
#
mesh = rs.Mesh(model,
                crs_uuid = crs.uuid,
                mesh_flavour = 'reg&z',
                ni = ni,
                nj = nj,
                origin = origin,
                dxyz_dij = np.array([[di, 0.0, 0.0], [0.0, dj, 0.0]]),
                z_values = z,
                title = 'test_from_gri_file',
                originator = 'pss',
                extra_metadata = {'testing mode': 'automated'})

assert mesh is not None
mesh.write_hdf5()
mesh.create_xml()
mesh_uuid = mesh.uuid

# fully write model to disc
model.store_epc()
epc_file = model.epc_file


#
# re-open model and assert that our Grid object is there
model2 = rq.Model(epc_file)
assert (model2.uuid(obj_type = 'Grid2dRepresentation', title = 'test_from_gri_file')) == mesh_uuid

mesh_uuid = model2.uuid(obj_type = 'Grid2dRepresentation', title = 'test_from_gri_file')



#
# Write the RESQML data to the ETP server
# 

# # Use with locally running open-etp-server 
# args = {
#     'host': '127.0.0.1',
#     'port': '9002',
#     'token': None
# }

# jwt_token = None
jwt_token = "..."  # get token from azure CLI.  User must be part of the AAD group "rddms-users"


args = {
    'host': 'interop-rddms.azure-api.net',
    'port': '443',
    'token': jwt_token
}


wsm = openWebSocket(
    serv_url = args['host'],
    serv_port = args['port'],
    serv_sub_path = None,
    serv_token = args['token'],
)

dataspace = "demo/pss"

#
# start from a clean dataspace (delete and recreate the dataspace)
#
gds = asyncio.run(
    deleteDataspace(wsm, dataspace)
)
gds = asyncio.run(
    addDataspace(wsm, dataspace)
)

#
# write the data object.  this does not yet write the data array.
#
pdo = asyncio.run(
    putDataObject(wsm, epc_file, dataspace)
)

#
# create a etpproto-specific dict for the "put data object array" call
# TODO: improce the XML parsing here..
#
pathInResource = model2.roots()[1][3][3][1][1][0][0].text

url = f'eml:///dataspace(\'{dataspace}\')/eml20.EpcExternalPartReference({str(mesh.uuid)})'
# dims = list(mysurf.values.data.shape)
# vals = mysurf.values.data.flatten().tolist()
dims = list(z.shape)
vals = z.flatten().tolist()
put_array_dict = {'dataArrays': {'0': {'uid': {'uri': url, 'pathInResource': pathInResource}, 'array': {'dimensions': dims, 'data':{'item':{'values': vals }}}, 'customData':{}}}}

pdoa = asyncio.run(
    putDataObjectArray(wsm, put_array_dict)
)



#
# read data back in using etpclient-python;  write the resulting data into .epc and .h5 files
#

import urllib.parse    
import zipfile
import h5py
from lxml import etree

guid4 = str(mesh_uuid)  # Guid of target object
dot4 = 'resqml20.obj_Grid2dRepresentation'   # data object type of target object
obj_type_label = dot4.split('.')[1]
part_name = f'{obj_type_label}_{guid4}'
epc_out_file = 'test1-returned'
part_names = []
PathInHdfFile = ""
res2 = None

gds = asyncio.run( getDataspaces(wsm) )
for ii,ds in enumerate(gds):
    if (dataspace == ds.path):
        res0 = asyncio.run( getResources(wsm, ds.uri) )
        # print("res0", type(res0))
        for res in res0:
            res1 = asyncio.run( getDataObject(wsm, res.uri ) )
            vv = list(res1.values())[0]
            # print("res1[0]", type(vv), vv.resource, dir(vv))
            if (guid4 in vv.resource.uri and 'Grid2dRepresentation' in vv.resource.uri):
                object_xml_2 = vv.data
                root = etree.fromstring(object_xml_2)
                PathInHdfFile = root[3][3][1][1][0][0].text  # NOTE: same structure as line pathInResource above..
                root[3][3][1][1].tag   ## references the name of the DataArray (should be ZValues)
                uri = f'eml:///dataspace(\'{dataspace}\')/eml20.EpcExternalPartReference({str(mesh_uuid)})'
                res2 = asyncio.run( getDataArray(wsm, uri, PathInHdfFile ) )
                # print("mesh uuid", str(mesh_uuid))
                # print("res2", type(res2) )
                # print("res2", res2.shape )

if len(PathInHdfFile)>0 and res2 is not None:
    # 
    # Create the .epc file; i.e. a zip archive containing multiple .xml files
    #
    with zipfile.ZipFile(epc_out_file+'.epc', 'w') as myzip:
        myzip.writestr(part_name+".xml", object_xml_2)
        part_names.append(part_name)

    #
    # Create a HDF5 file that contains the relevant arrays
    #
    with h5py.File(epc_out_file+'.h5', 'w') as h5f:
        hdf5path = PathInHdfFile
        dim = res2.shape
        # array_np = np.reshape( np.array(array_data['data']['data']), dim )
        h5f.create_dataset(hdf5path, data=res2)

    #
    # The .epc file must contain a list of constitutent parts in XML format: write it here
    #        
    cts = []
    cts.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    cts.append('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">')
    for pn in part_names:
        obj_type_label = "_".join( pn.split("_")[:-1] )
        ss = f'<Override PartName="/{pn}.xml" ContentType="application/x-resqml+xml;version=2.0;type={obj_type_label}"/>'
        cts.append("   "+ss)
    cts.append('</Types>')

    # append our list of content to the .epc (zip) file
    with zipfile.ZipFile(epc_out_file+'.epc', 'a') as myzip:
        myzip.writestr('[Content_Types].xml', "\n".join(cts))


#
# Use resqpy to read the written RESQML model (.epc and .h5 file)
#

model_out = rq.Model(epc_out_file+".epc")

g = model_out.uuid(obj_type = 'Grid2dRepresentation')
assert g==mesh_uuid
m = rs.Mesh(model_out,g)  # reads the Grid2dRepresentation as a resqpy Mesh model, but does not yet load the array binary data
m.full_array_ref()    # forces loading of array binary data (from .h5 file)
# print(m.full_array.shape)  

#
# check that round-tripped array is unchanged (apart from missing/invalid values)
#
v0 = mysurf.values.data
v1 = m.full_array[:,:,2]
v0[v0>9e32] = 0  # the input data uses 1e33 for missing values. this values comes out different after the round-trip (conversion to single-precision?) 
v1[v1>9e32] = 0  #
assert np.amax(np.abs(v0-v1)) < 1e-6   # round-tripped array is equal to input

