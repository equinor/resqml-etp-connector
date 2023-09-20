import numpy as np
import time

#
# NOTE: this code requires a patched version of etpclient
#

from etpclient.websocket_manager import WebSocketManager
from etpclient.etp.requester import *
# import etpclient.etp.serverprotocols

from etptypes.energistics.etp.v12.protocol.data_array.put_data_arrays import (
    PutDataArrays,
)


def openWebSocket(
    serv_url=None,
    serv_port=None,
    serv_sub_path=None,
    serv_token=None,   
):
    use_wss = 'azure' in serv_url
    serv_uri = (
        str(serv_url)
        + (":" + str(serv_port) if serv_port else "")
        + "/"
        + (serv_sub_path + "/" if serv_sub_path else "")
    )
    protocol = "wss://" if use_wss else "ws://"
    wsm = WebSocketManager(
        protocol + serv_uri,
        username=None,
        password=None,
        token=serv_token if use_wss else None,
    )
    cpt_wait = 0
    time_step = 0.01
    while not wsm.is_connected() and (cpt_wait * time_step < 30):
        # if (cpt_wait * 1000 % 1000) < 5:
        #     print("\rwait for connection" + wait_symbol(cpt_wait), end="")
        cpt_wait = cpt_wait + 1
        time.sleep(time_step)

    running = wsm.is_connected()

    if not running and (cpt_wait * time_step >= 30):
        print("Timeout...")
    return wsm

async def getDataspaces(
    wsm=None,
):
    result = await wsm.send_and_wait(get_dataspaces())
    if result:
        print("==== type ", type(result))
        pass
    else:
        print("No answer...")
    return result

async def deleteDataspace(
    wsm, dataspacePath,
):
    result = await wsm.send_and_wait(delete_dataspace([dataspacePath]))
    if result:
        # pretty_p.pprint(result)
        pass
    else:
        print("No answer...")

async def addDataspace(
    wsm, dataspacePath,
):
    gds = await getDataspaces(wsm)
    existingPaths = [ds.path for ds in gds]
    if (dataspacePath in existingPaths):
        print("Requested Dataspace already exists on server", dataspacePath )
        return
    if (dataspacePath not in existingPaths):
        # print("Trying to add Dataspace", dataspacePath)
        try:
            result = await wsm.send_and_wait(put_dataspace([dataspacePath]))
        except Exception as e:
            print(e)
    return

async def putDataObject(
    wsm, file, dataspace
):
    for putDataObj in put_data_object_by_path(
        file, dataspace
    ):
        result = await wsm.send_no_wait(putDataObj)
        if result:
            # pretty_p.pprint(result)
            pass
        else:
            print("No answer...")

async def putDataObjectArray(
    wsm, pda_dict,
):
    uuid_list = []
    res=[]
    res.append(PutDataArrays.parse_obj(pda_dict))
    for pda in res:
        print("PDA: put data array", type(pda))
        try:
            await wsm.send_and_wait(pda)
        except Exception as e:
            print("ERROR : ", e)
    # async for msg_idx in res:
    #     print(msg_idx)


async def getResources(
    wsm, uri, depth=1,
):
    result = await wsm.send_and_wait(
        get_resouces(
            uri, 
            depth
        )
    )
    if result:
        # pretty_p.pprint(result.body.__dict__)
        # get_res_resp
        pass
    else:
        print("No answer...")
    return result


async def getDataArray(
    wsm, uri, pir
):
    get_data_arr = get_data_array( uri,pir )
    print(f"\n\n{get_data_arr}\n\n")
    result = await wsm.send_and_wait(get_data_arr)
    return result

async def getDataArrayMetadata(
    wsm, uri, pir
):
    get_data_arr = get_data_array_metadata(uri, pir)
    result = await wsm.send_and_wait(get_data_arr)
    if result:
        pass
    else:
        print("No answer...")

async def getDataObject(
    wsm, uri
):
    get_data_obj = get_data_object([uri])
    # print("Sending : ", get_data_obj.__dict__)
    result = await wsm.send_and_wait(get_data_obj)
    if result:
        print(type(result))
        pass
    else:
        print("No answer...")
    return result

