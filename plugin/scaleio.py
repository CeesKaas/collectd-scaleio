# Collectd plugin for ScaleIO

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4 

import collectd
import traceback
import types
import json
import requests

CONF = {
    'debug':              False,
    'verbose':            False,
    'gateway':            '',
    'cluster':            'myCluster',
    'pools':              [],
    'mdmuser':            '',
    'mdmpassword':        '',
}

def config_callback(conf):
    collectd.debug('config callback')
    for node in conf.children:
        key = node.key.lower()
        values = node.values
        collectd.debug('Reading config %s: %s' % (key, " ".join(str(v) for v in values)))

        if key == 'debug':
            CONF['debug'] = str2bool(values[0])
        elif key == 'verbose':
            CONF['verbose'] = str2bool(values[0])
        elif key == 'gateway':
            CONF['gateway'] = values[0]
        elif key == 'cluster':
            CONF['cluster'] = values[0]
        elif key == 'pools':
            CONF['pools'] = values
        elif key == 'mdmuser':
            CONF['mdmuser'] = values[0]
        elif key == 'mdmpassword':
            CONF['mdmpassword'] = values[0]
        else:
            collectd.warning('ScaleIO: unknown config key: %s' % (key))

def init_callback():
    my_debug('init callback')

def read_callback(input_data=None):
    try:
        session_id = gw_login(CONF['gateway'], CONF['mdmuser'], CONF['mdmpassword'])
        sio_all_pools = sio_get_pools(CONF['gateway'], CONF['mdmuser'], session_id)
        all_pools_metrics = gw_req_metrics(CONF['gateway'], CONF['mdmuser'], session_id)
        sio_2proc_pools = sio_select_pools(sio_all_pools, CONF['pools'])
        sio_parse_metrics(all_pools_metrics, sio_2proc_pools)
        gw_logout(CONF['gateway'], CONF['mdmuser'], session_id)
    except Exception as e:
        collectd.error('read_callback failed: %s' % e)
        return

# Dispatch values to collectd
def dispatch_value(plugin, value, plugin_instance=None, type_instance=None):
    val = collectd.Values(type = 'gauge')

    my_verbose('Dispatch value: %s %s %s %s %s' % (CONF['cluster'], plugin, plugin_instance, type_instance, value))
    val.host = CONF['cluster']
    val.plugin = 'scaleio_' + plugin
    val.plugin_instance = plugin_instance
    val.type_instance = type_instance
    val.values = [value]
    val.dispatch()

# ScaleIO Gateway - Function used for GET and POST requests to the gateway
def gw_request(login, password, url, headers, data, method):
    if ( method == "GET" ):
        headers = {'Connection': 'close'}
        try:
            response = requests.get(url, auth=(login, password), verify=False, headers=headers)
        except requests.exceptions.RequestException as e:
            my_verbose('Error establishing connection to the ScaleIO Gateway. Check your collectd module configuration. Exiting.')
            raise
    elif ( method == "POST" ):
        headers = {'Content-type': 'application/json', 'Connection': 'close'}
        try:
            response = requests.post(url, data=json.dumps(data), auth=(login, password), verify=False, headers=headers)
        except requests.exceptions.RequestException as e:
            my_verbose('Error establishing connection to the ScaleIO Gateway. Check your collectd module configuration. Exiting.')
            raise
    return response

# ScaleIO Gateway - Login Function
def gw_login(gw_address, login, password):
    url = 'https://%s/api/login' % gw_address
    headers = {'Connection': 'close'}
    session_id = gw_request(login, password, url, headers, None, "GET")
    if ( session_id.status_code == 401 ):
        my_verbose('Error authenticating to the ScaleIO Gateway. Wrong credential supplied. Check your collectd module configuration. Exiting.')
        raise
    return session_id.text.replace("\"", "")

# ScaleIO Gateway - Logout Function
def gw_logout(gw_address, login, session_id):
    url = 'https://%s/api/logout' % gw_address
    headers = {'Connection': 'close'}
    logout = gw_request(login, session_id, url, headers, None, "GET")
    if ( logout.status_code == 401 ):
        my_verbose('Error authenticating to the ScaleIO Gateway. SessionID used for REST-API authentication is expired or invalid. Check your collectd module configuration. Exiting.')
        raise

# ScaleIO Gateway - Request metrics that will be parsed and dispatched to collectd
def gw_req_metrics(gw_address, login, session_id):
    url = 'https://%s/api/types/StoragePool/instances/action/querySelectedStatistics' % gw_address
    headers = {'Content-type': 'application/json', 'Connection': 'close'}
    data = { 'allIds': '', 'properties': [ 'maxCapacityInKb', 'capacityAvailableForVolumeAllocationInKb', 'capacityInUseInKb', 
             'thinCapacityAllocatedInKm', 'thickCapacityInUseInKb', 'snapCapacityInUseOccupiedInKb', 'unreachableUnusedCapacityInKb', 
             'degradedHealthyCapacityInKb', 'failedCapacityInKb', 'spareCapacityInKb', 'primaryReadBwc', 'primaryWriteBwc', 
             'rebalanceReadBwc', 'fwdRebuildReadBwc', 'bckRebuildReadBwc' ] }   
    metrics = gw_request(login, session_id, url, headers, data, "POST")
    if ( metrics.status_code == 401 ):
        my_verbose('Error authenticating to the ScaleIO Gateway. SessionID used for REST-API authentication is expired or invalid. Check your collectd module configuration. Exiting.')
        raise
    metrics = json.loads(json.dumps(metrics.json()))
    return metrics

# ScaleIO Gateway - Get StoragePools list (name, ID)
def sio_get_pools(gw_address, login, session_id):
    sio_all_pools = []
    url = 'https://%s/api/types/StoragePool/instances' % gw_address
    headers = { 'Connection': 'close' }
    pools = gw_request(login, session_id, url, headers, None, "GET")
    if ( pools.status_code == 401 ):
        my_verbose('Error authenticating to the ScaleIO Gateway. SessionID used for REST-API authentication is expired or invalid. Check your collectd module configuration. Exiting.')
        raise
    pools = json.loads(json.dumps(pools.json()))
    for i in range ( 0, len(pools) ):
        sio_all_pools.append([pools[i]['name'], pools[i]['id']])
    return sio_all_pools

# Select StoragePools to be processed according configuration
def sio_select_pools(sio_all_pools, sio_req_pools):
    sio_2proc_pools = []
    for i in range ( 0, len(sio_req_pools) ):
        found = False
        for j in range ( 0, len(sio_all_pools) ):
            if ( sio_req_pools[i] == sio_all_pools[j][0] ):
                sio_2proc_pools.append([sio_all_pools[j][0], sio_all_pools[j][1]])
                found = True
        if found == False:
            my_verbose('Requested pool: "%s" doesn\'t exist on your ScaleIO System. Check your collectd module configuration.\n' % sio_req_pools[i])
    if ( len(sio_2proc_pools) == 0 ):
        my_verbose('Can\'t find any requested pool to process on your ScaleIO System. Check your collectd module configuration. Exiting.')
        raise
    return sio_2proc_pools

# Parse JSON objects returned by ScaleIO gateway and prepares values to be dispatched to collectd
def sio_parse_metrics(all_pools_metrics, sio_2proc_pools):
    for i in range ( 0, len(sio_2proc_pools) ):
        read_iops = read_bps = write_iops = write_bps = rebalance_iops = rebalance_bps = fwd_rebuild_iops = fwd_rebuild_bps = bck_rebuild_iops = bck_rebuild_bps = total_iops = 0

        current_pool_metrics = all_pools_metrics[sio_2proc_pools[i][1]]

        # raw capacity
        raw_bytes = KB_to_Bytes(current_pool_metrics['maxCapacityInKb'] / 2)
        dispatch_value('pool', raw_bytes, sio_2proc_pools[i][0], 'raw_bytes')

        # useable capacity
        useable_bytes = KB_to_Bytes(current_pool_metrics['capacityAvailableForVolumeAllocationInKb'] + 
                                    current_pool_metrics['capacityInUseInKb'] / 2)
        dispatch_value('pool', useable_bytes, sio_2proc_pools[i][0], 'useable_bytes')

        # available capacity
        available_bytes = KB_to_Bytes(current_pool_metrics['capacityAvailableForVolumeAllocationInKb'])
        dispatch_value('pool', available_bytes, sio_2proc_pools[i][0], 'available_bytes')

        # used capacity
        used_bytes = KB_to_Bytes(current_pool_metrics['capacityInUseInKb'] / 2)
        dispatch_value('pool', used_bytes, sio_2proc_pools[i][0], 'used_bytes')

        # allocated capacity
        allocated_bytes = (KB_to_Bytes(current_pool_metrics['thinCapacityAllocatedInKm']) + 
                           KB_to_Bytes(current_pool_metrics['thickCapacityInUseInKb']) + 
                           KB_to_Bytes(current_pool_metrics['snapCapacityInUseOccupiedInKb'])) / 2
        dispatch_value('pool', allocated_bytes, sio_2proc_pools[i][0], 'allocated_bytes')

        # unreachable unused capacity
        unreachable_unused_bytes = KB_to_Bytes(current_pool_metrics['unreachableUnusedCapacityInKb'])
        dispatch_value('pool', unreachable_unused_bytes, sio_2proc_pools[i][0], 'unreachable_unused_bytes')

        # degraded capacity
        degraded_bytes = KB_to_Bytes(current_pool_metrics['degradedHealthyCapacityInKb'])
        dispatch_value('pool', degraded_bytes, sio_2proc_pools[i][0], 'degraded_bytes')

        # failed capacity
        failed_bytes = KB_to_Bytes(current_pool_metrics['failedCapacityInKb'])
        dispatch_value('pool', failed_bytes, sio_2proc_pools[i][0], 'failed_bytes')

        # spare capacity
        spare_bytes = KB_to_Bytes(current_pool_metrics['spareCapacityInKb'])
        dispatch_value('pool', spare_bytes, sio_2proc_pools[i][0], 'spare_bytes')

        # read IOPS / read throughput
        t_read_metrics = current_pool_metrics['primaryReadBwc']

        t_read_iops = t_read_metrics['numOccured']
        t_read_bytes = KB_to_Bytes(t_read_metrics['totalWeightInKb'])
        t_read_nsec = t_read_metrics['numSeconds']
        if ( t_read_iops != 0 ):
            read_iops = ( t_read_iops / t_read_nsec )
            read_bps = ( t_read_bytes / t_read_nsec )

        dispatch_value('pool', read_iops, sio_2proc_pools[i][0], 'read_iops')
        dispatch_value('pool', read_bps, sio_2proc_pools[i][0], 'read_bps')

        # write IOPS / write throughput
        t_write_metrics = current_pool_metrics['primaryWriteBwc']

        t_write_iops = t_write_metrics['numOccured']
        t_write_bytes = KB_to_Bytes(t_write_metrics['totalWeightInKb'])
        t_write_nsec = t_write_metrics['numSeconds']
        if ( t_write_iops != 0 ):
            write_iops = ( t_write_iops / t_write_nsec )
            write_bps = ( t_write_bytes / t_write_nsec )

        dispatch_value('pool', write_iops, sio_2proc_pools[i][0], 'write_iops')
        dispatch_value('pool', write_bps, sio_2proc_pools[i][0], 'write_bps')

        # rebalance IOPS / rebalance throughput
        t_rebal_read_metrics = current_pool_metrics['rebalanceReadBwc']

        t_rebal_read_iops = t_rebal_read_metrics['numOccured']
        t_rebal_read_bytes = KB_to_Bytes(t_rebal_read_metrics['totalWeightInKb'])
        t_rebal_read_nsec = t_rebal_read_metrics['numSeconds']
        if ( t_rebal_read_iops != 0 ):
            rebalance_iops = ( t_rebal_read_iops / t_rebal_read_nsec )
            rebalance_bps = ( t_rebal_read_bytes / t_rebal_read_nsec )

        dispatch_value('pool', rebalance_iops, sio_2proc_pools[i][0], 'rebalance_iops')
        dispatch_value('pool', rebalance_bps, sio_2proc_pools[i][0], 'rebalance_bps')

        # rebuild IOPS / rebuild throughput
        t_fwdrebui_read_metrics = current_pool_metrics['fwdRebuildReadBwc']

        t_fwdrebui_read_iops = t_fwdrebui_read_metrics['numOccured']
        t_fwdrebui_read_bytes = KB_to_Bytes(t_fwdrebui_read_metrics['totalWeightInKb'])
        t_fwdrebui_read_nsec = t_fwdrebui_read_metrics['numSeconds']
        if ( t_fwdrebui_read_iops != 0 ):
            fwd_rebuild_iops = t_fwdrebui_read_iops / t_fwdrebui_read_nsec
            fwd_rebuild_bps = t_fwdrebui_read_bytes / t_fwdrebui_read_nsec

        t_bckrebui_read_metrics = current_pool_metrics['bckRebuildReadBwc']

        t_bckrebui_read_iops = t_bckrebui_read_metrics['numOccured']
        t_bckrebui_read_bytes = KB_to_Bytes(t_bckrebui_read_metrics['totalWeightInKb'])
        t_bckrebui_read_nsec = t_bckrebui_read_metrics['numSeconds']
        if ( t_bckrebui_read_iops != 0 ):
            bck_rebuild_iops = t_bckrebui_read_iops / t_bckrebui_read_nsec
            bck_rebuild_bps = t_bckrebui_read_bytes / t_bckrebui_read_nsec

        rebuild_iops = fwd_rebuild_iops + bck_rebuild_iops
        rebuild_bps = fwd_rebuild_bps + bck_rebuild_bps
        dispatch_value('pool', rebuild_iops, sio_2proc_pools[i][0], 'rebuild_iops')
        dispatch_value('pool', rebuild_bps, sio_2proc_pools[i][0], 'rebuild_bps')

def KB_to_Bytes(value):
    return value * 1024 ** 1

def str2bool(v):
    if type(v) == type(True):
        return v
    return v.lower() in ("yes", "true", "t", "1")

def my_debug(msg):
    if CONF['debug']:
        collectd.info('ScaleIO: %s' % (msg))

def my_verbose(msg):
    if CONF['verbose']:
        collectd.info('ScaleIO: %s' % (msg))

# register callback functions
collectd.register_config(config_callback)
collectd.register_init(init_callback)
collectd.register_read(read_callback)
