#!/bin/python

"""Create/Delete a vm using openstack sdk"""

import os
import time
import socket
import logging
import functools
import openstack
import os.path
import configparser
from novaclient import client

from helper import config, load_config, setup_logging
from pyzabbix_socketwrapper import PyZabbixPSKSocketWrapper
from pyzabbix import ZabbixSender, ZabbixMetric

FileName = "/home/rado/create-delete-openstack-vms/error_logs_test/"
FileName += time.strftime("%d-%b-%Y")
FileName += ".log"

with open(FileName, "a+") as f:
    pass

logging = setup_logging("vm_error",FileName)

CLOUDNAME = "kaizen_oidc"
SERVERNAME = "test.vm"
IMAGENAME = "centos7-1907"
FLAVOR = "m1.small"
NETWORK = "default_network"
POOL = "external"
KEYPAIR_NAME = "pk"

vm_status = []

conn = openstack.connect(cloud=CLOUDNAME)
nova_client = client.Client(2, session=conn)


class Lock:
    """ This class will have lock mechanism to ensures that the script does not run if previous vm is not deleted"""

    def enable(self):
        with open("vm_check", "w+") as f:
            pass

    def allow(self):
        return os.path.isfile('vm_check')

    def disable(self):
        os.remove('vm_check')
        return True

  
lock = Lock()

def create_instance(conn):
    """Create a vm + assing floating ip + SSH in the VM"""

    try:
        image = conn.compute.find_image(IMAGENAME)
        flavor = conn.compute.find_flavor(FLAVOR)
        network = conn.network.find_network(NETWORK)
        keypair = conn.compute.find_keypair(KEYPAIR_NAME)

        instance = conn.compute.create_server(
            name=SERVERNAME,
            image_id=image.id,
            flavor_id=flavor.id,
            networks=[{"uuid": network.id}],
            key_name=keypair.name,
            timeout=600,
        )

        instance = conn.compute.wait_for_server(instance)
        lock.enable() # Lock is enabled when vm is created
        vm_status.append('vm_created:Success, ')

    except Exception as err:
        vm_status.append('vm_created:Failed, ')
        logging.error(err)
        return None

    try:
        instance = conn.compute.wait_for_server(instance)
        ip = conn.available_floating_ip()

        conn.compute.add_floating_ip_to_server(
            server=instance.id,
            address=ip.floating_ip_address)

        vm_status.append('vm_ip_assigned:Success, ')

    except Exception as err:
        vm_status.append('vm_ip_assigned:Failed, ')
        logging.error(err)
        return None

    time.sleep(120)  # Sleeping to make sure IP is associated properly

    while True:
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.connect((ip.floating_ip_address, 22))
            vm_status.append('vm_ssh_connection:Success, ')
            break
        except Exception as err:
            vm_status.append('vm_ssh_connection:Failed, ')
            logging.error(err)
            return None
            break
        finally:
            test_socket.close()
    return instance


def delete_instance(conn, instance):
    """Deletes the VM + floating IP sent to the pool"""

    try:
        conn.compute.delete_server(instance.id, 850)
        vm_status.append('vm_deletion:Success ')
        return lock.disable() # Lock is disabled when the vm deletion is succesfull

    except Exception as err:
        vm_status.append('vm_deletion:Failed ')
        logging.error(err)
        return False


def config_function():
    """Zabbix sender config"""

    load_config()
    PSK_IDENTITY = config['zabbix_api']['PSK_IDENTITY']
    PSK = config['zabbix_api']['PSK']
    ZABBIX_SERVER = config['zabbix_api']['ZABBIX_SERVER']

    custom_wrapper = functools.partial(
        PyZabbixPSKSocketWrapper, identity=PSK_IDENTITY, psk=bytes(bytearray.fromhex(PSK)))
    zabbix_sender = ZabbixSender(
        zabbix_server=ZABBIX_SERVER, socket_wrapper=custom_wrapper, timeout=30)
    return zabbix_sender


if __name__ == "__main__":

    if lock.allow():
        """ Checks if the file exists before executing the script"""
        logging.error('DANGER: Latest VM not deleted')
        exit()

    zabbix_sender = config_function()
    instance = create_instance(conn)
    if instance is not None:
        delete_instance(conn, instance)
    s = "".join(vm_status)
    #print(s)
    zabbix_sender.send(
        [ZabbixMetric("openstack-monitoring", "openstack.test", s)])
