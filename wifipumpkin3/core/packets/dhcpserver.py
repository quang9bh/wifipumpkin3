import logging
import socket
from dhcplib.packet import DHCPPacket, _FORMAT_CONVERSION_DESERIAL, DHCP_OPTIONS_TYPES
from dhcplib import net, getifaddrslib
import ipaddress as ip
from queue import Queue

log = logging.getLogger(__name__)
from PyQt5.QtCore import QThread, pyqtSignal, pyqtSlot, QProcess, QObject
from wifipumpkin3.core.config.globalimport import *
from wifipumpkin3.core.utility.printer import display_messages

# This file is part of the wifipumpkin3 Open Source Project.
# wifipumpkin3 is licensed under the Apache 2.0.

# Copyright 2020 P0cL4bs Team - Marcos Bomfim (mh4x0f)

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


class IpAddressClass(object):
    """ class for generator ipaddress """

    def __init__(self, range):
        self.range = range
        self.inital_range = ip.ip_address(range.split("/")[0])
        self.end = int(self.get_lastOctet(range.split("/")[1]))
        self.ipaddres_list = []
        self.createRangeIp(self.end)

    def createRangeIp(self, end):
        while str(self.inital_range) != self.range.split("/")[1]:
            self.inital_range += 1
            self.ipaddres_list.append(self.inital_range)

    def get_lastOctet(self, ipaddress):
        return ipaddress.split(".")[-1]

    def add_IpAdressNotUse(self, ip):
        self.ipaddres_list.insert(0, ip)

    def __iter__(self):
        return self

    def __next__(self):
        if len(self.ipaddres_list) > 0:
            return self.ipaddres_list.pop(0)
        return None


class DHCPProtocol(QObject):
    _request = pyqtSignal(object)

    def __init__(self, DHCPConf):
        QObject.__init__(self)
        self.dhcp_conf = DHCPConf
        self.IPADDR = iter(IpAddressClass(self.dhcp_conf["range"]))
        self.leases = {}
        self.queue = Queue()
        self.message = []
        self.started = True

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        packet = DHCPPacket(data)
        log.debug("RECV from %s:\n%s\n", addr, packet)
        send = False
        mac = str(packet.get_hardware_address())
        if mac not in self.leases.keys():
            self.ip_client = next(self.IPADDR)
            self.leases[mac] = {"MAC": mac, "IP": str(self.ip_client),"HOSTNAME" : "" }
        else:
            self.ip_client = self.leases[mac]["IP"]

        if packet.is_dhcp_discover_packet():
            log.debug("DISCOVER")
            packet.transform_to_dhcp_offer_packet()
            # self._request.emit(packet.)
            packet.set_option("yiaddr", self.ip_client)
            packet.set_option("siaddr", self.dhcp_conf["router"])
            # self._request.emit(packet.__str__())
            send = True
        elif packet.is_dhcp_request_packet():
            log.debug("REQUEST")
            packet.transform_to_dhcp_ack_packet()
            packet.set_option("yiaddr", self.ip_client)
            packet.set_option("siaddr", self.dhcp_conf["router"])
            packet.set_option("router", [self.dhcp_conf["router"]], validate=False)
            packet.set_option(
                "domain_name_servers", [self.dhcp_conf["router"]], validate=False
            )
            packet.set_option(
                "ip_address_lease_time", int(self.dhcp_conf["leasetimeMax"])
            )
            for key in self.leases.keys():
                for item in self.leases[key].keys():
                    if self.leases[key][item] == str(self.ip_client):
                        self.leases[key]["HOSTNAME"] = self.getHostnamePakcet(packet)
                        break
            self._request.emit(self.leases[mac])
            self.queue.put(self.leases[mac])
            Refactor.writeFileDataToJson(C.CLIENTS_CONNECTED, self.leases, "w")
            send = True
        if send:
            log.debug("SEND to %s:\n%s\n", addr, packet)
            ipaddr, port = addr
            if ipaddr == "0.0.0.0":
                ipaddr = "255.255.255.255"
            addr = (ipaddr, port)
            try:
                self.transport.sendto(packet.encode_packet(), addr)
            except:
                pass

    def getHostnamePakcet(self, packet):
        for (option_id, data) in sorted(packet._options.items()):
            result = None
            if option_id == 53:  # dhcp_message_type
                pass
            elif option_id == 55:  # parameter_request_list
                pass
            else:
                result = _FORMAT_CONVERSION_DESERIAL[DHCP_OPTIONS_TYPES[option_id]](
                    data
                )
            if packet._get_option_name(option_id) == "hostname":
                return result

    def error_received(exc):
        log.error("ERROR", exc_info=exc)


class DHCPThread(QThread):
    def __init__(self, iface, DHCPconf):
        QThread.__init__(self)
        self.iface = iface
        self.dhcp_conf = DHCPconf
        self.DHCPProtocol = DHCPProtocol(self.dhcp_conf)
        self.started = False

    def run(self):
        self.started = True
        logging.basicConfig()
        log = logging.getLogger("dhcplib")
        log.setLevel(logging.DEBUG)

        server_address = self.dhcp_conf["router"]
        server_port = 67
        client_port = 68

        # log.debug('Listen on %s:%s (-> %s)', server_address, server_port, client_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", server_port))
        self.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_BINDTODEVICE, str(self.iface + "\0").encode()
        )

        print(display_messages("starting {}".format(self.objectName()), info=True))

        # self.DHCP._request.connect(self.get_DHCP_Response)
        self.DHCPProtocol.connection_made(self.sock)
        # log.debug("Starting UDP server")
        while self.started:
            try:
                message, address = self.sock.recvfrom(1024)
                self.DHCPProtocol.datagram_received(message, address)
            except Exception as e:
                # OSError: [Errno 9] Bad file descriptor when close socket
                pass

    def getpid(self):
        """ return the pid of current process in background"""
        return "thread"

    def getID(self):
        """ return the name of process in background"""
        return self.objectName()

    def stop(self):
        self.started = False
        Refactor.writeFileDataToJson(C.CLIENTS_CONNECTED, {}, "w")
        print("Thread::[{}] successfully stopped.".format(self.objectName()))
        self.sock.close()
