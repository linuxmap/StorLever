"""
storlever.mngr.utils.ntpmgr
~~~~~~~~~~~~~~~~

This module implements ntp server management.

:copyright: (c) 2013 by jk.
:license: GPLv3, see LICENSE for more details.

"""

import os
import os.path
import subprocess

from storlever.lib.config import Config
from storlever.lib.command import check_output
from storlever.lib.exception import StorLeverError
from storlever.lib import logger
import logging
from storlever.lib.schema import Schema, Use, Optional, \
    Default, DoNotCare, BoolVal, IntVal

from storlever.lib.lock import lock
from storlever.mngr.system.cfgmgr import STORLEVER_CONF_DIR, cfg_mgr
from storlever.mngr.system.servicemgr import service_mgr


NTP_CONF_FILE_NAME = "ntp_conf.yaml"
NTP_ETC_CONF_DIR = "/etc/"
NTP_ETC_CONF_FILE = "ntp.conf"
NTP_ETC_STORLEVER_CONF_DIR = "/etc/ntp/"
NTP_ETC_STORLEVER_CONF_FILE = "ntp_storlever.conf"
NTPQ_CMD = "/usr/sbin/ntpq"


NTP_SERVER_CONF_SCHEMA = Schema({
    # it can be a ipv4 address, ipv6 address, or host dns name
    "server_addr": Use(str),
    # if set to True, it would be forced to resolve the host name to
    # ipv6 address in DNS resolution
    Optional("ipv6"):  Default(BoolVal(), default=False),

    # Marks the server as preferred.  All other things being equal,
    # this host will be chosen for synchronization among set of correctly operating hosts
    Optional("prefer"): Default(BoolVal(), default=True),

    # Specifies a mode number which is interpreted in a device
    # specific fashion.	For instance, it selects a dialing,
    # protocol in the ACTS driver and a device subtype in the
    # parse drivers.
    # Only valid for reference clock server, i.e. server_addr is 127.127.t.n
    Optional("mode"): Default(IntVal(min=0, max=65535), default=0),


    # Specifies the stratum number assigned to the driver, an
    # integer between 0 and 15.	This number overrides the
    # default stratum number ordinarily assigned	by the driver
    # itself, usually zero.
    # Only valid for reference clock server, i.e. server_addr is 127.127.t.n
    Optional("stratum"): Default(IntVal(min=0, max=15), default=0),

    # These four	flags are used for customizing the clock
    # driver.  The interpretation of these values, and whether
    # they are used at all, is a	function of the	particular
    # clock driver.  However, by	convention flag4 is used to
    # enable recording monitoring data to the clockstats	file
    # configured	with the filegen command.  Further information
    # on	the filegen command can	be found in Monitoring
    # Options.
    # Only valid for reference clock server, i.e. server_addr is 127.127.t.n
    Optional("flag1"): Default(IntVal(min=0, max=1), default=0),
    Optional("flag2"): Default(IntVal(min=0, max=1), default=0),
    Optional("flag3"): Default(IntVal(min=0, max=1), default=0),
    Optional("flag4"): Default(IntVal(min=0, max=1), default=0),

    DoNotCare(str): Use(str)  # for all those key we don't care
})

NTP_RESTRICT_CONF_SCHEMA = Schema({

    # it can be a ipv4 address, ipv6 address, or "default"
    "restrict_addr": Use(str),

    # if set to True, it would be forced to resolve the host name to
    # ipv6 address in DNS resolution
    Optional("ipv6"):  Default(BoolVal(), default=False),

    # mask the restrict_addr to indicate the network address.
    # default is empty, which is equal to 255.255.255.255
    Optional("mask"): Default(Use(str), default=""),


    # Deny packets of all kinds,	including ntpq(8) and ntpdc(8) queries
    Optional("ignore"): Default(BoolVal(), default=False),

    # Deny ntpq(8) and ntpdc(8) queries which attempt to	modify
    # the state of the server (i.e., run	time reconfiguration).
    # Queries which return information are permitted
    Optional("nomodify"): Default(BoolVal(), default=False),

    # Deny ntpq(8) and ntpdc(8) queries. Time service is not affected
    Optional("noquery"): Default(BoolVal(), default=False),

    # Deny all packets except ntpq(8) and ntpdc(8) queries.
    Optional("noserve"): Default(BoolVal(), default=False),

    # Decline to	provide	mode 6 control message trap service to
    # matching hosts.  The trap service is a subsystem of the
    # ntpdq control message protocol which is intended for use
    # by	remote event logging programs
    Optional("notrap"): Default(BoolVal(), default=False),

    DoNotCare(str): Use(str)  # for all those key we don't care
})


NTP_CONF_SCHEMA = Schema({

    Optional("server_list"):  Default([NTP_SERVER_CONF_SCHEMA], default=[]),

    Optional("restrict_list"):  Default([NTP_RESTRICT_CONF_SCHEMA], default=[]),

    DoNotCare(str): Use(str)  # for all those key we don't care
})


class NtpManager(object):
    """contains all methods to manage NTP server in linux system"""

    def __init__(self):
        # need a mutex to protect create/delete bond interface
        self.lock = lock()
        self.conf_file = os.path.join(STORLEVER_CONF_DIR, NTP_CONF_FILE_NAME)
        self.ntp_server_conf_schema = NTP_SERVER_CONF_SCHEMA
        self.ntp_restrict_conf_schema = NTP_RESTRICT_CONF_SCHEMA
        self.ntp_conf_schema = NTP_CONF_SCHEMA

    def _load_conf(self):
        ntp_conf = {}
        cfg_mgr().check_conf_dir()
        if os.path.exists(self.conf_file):
            ntp_conf = \
                Config.from_file(self.conf_file, self.ntp_conf_schema).conf
        else:
            ntp_conf = self.ntp_conf_schema.validate(ntp_conf)
        return ntp_conf

    def _save_conf(self, ntp_conf):
        cfg_mgr().check_conf_dir()
        Config.to_file(self.conf_file, ntp_conf)

    def _server_conf_to_line(self, server_conf):

        line = "server"
        if server_conf["ipv6"] and \
                (not server_conf["server_addr"].startswith("127.127")):
            ipv6_modifier = " -6"

        line += " " + server_conf["server_addr"]

        if server_conf["prefer"]:
            line += " prefer"

        line += " iburst"

        if server_conf["server_addr"].startswith("127.127"):
            line += " mode %d\n" \
                    "fudge %s stratum %d mode %d " \
                    "flag1 %d flag2 %d flag3 %d flag4 %d" % \
                    (server_conf["mode"], server_conf["server_addr"],
                     server_conf["stratum"], server_conf["mode"],
                     server_conf["flag1"], server_conf["flag2"],
                     server_conf["flag3"], server_conf["flag4"])

        line += "\n"

        return line

    def _restrict_conf_to_line(self, restrict_conf):

        line = "restrict"
        if restrict_conf["ipv6"]:
            ipv6_modifier = " -6"

        line += " " + restrict_conf["restrict_addr"]

        if restrict_conf["mask"] != "":
            line += " mask %s" % restrict_conf["mask"]

        line += " kod"   #always kod

        if restrict_conf["ignore"]:
            line += " ignore"
        if restrict_conf["nomodify"]:
            line += " nomodify"
        if restrict_conf["noquery"]:
            line += " noquery"
        if restrict_conf["noserve"]:
            line += " noserve"
        if restrict_conf["nomodify"]:
            line += " nomodify"
        if restrict_conf["noserve"]:
            line += " noserve"

        line += "\n"
        return line

    def _sync_to_system_conf(self, ntp_conf):

        if not os.path.exists(NTP_ETC_CONF_DIR):
            os.makedirs(NTP_ETC_CONF_DIR)
        if not os.path.exists(NTP_ETC_STORLEVER_CONF_DIR):
            os.makedirs(NTP_ETC_STORLEVER_CONF_DIR)

        # write the etc storlever config
        file_name = os.path.join(NTP_ETC_STORLEVER_CONF_DIR, NTP_ETC_STORLEVER_CONF_FILE)
        with open(file_name, "w") as f:
            f.write("# server list\n")
            for server_conf in ntp_conf["server_list"]:
                f.write(self._server_conf_to_line(server_conf))
            f.write("\n\n")
            f.write("# restrict list\n")
            for restrict_conf in ntp_conf["restrict_list"]:
                f.write(self._restrict_conf_to_line(restrict_conf))

        # add storlever config to ntp.conf
        file_name = os.path.join(NTP_ETC_CONF_DIR, NTP_ETC_CONF_FILE)
        already_include = False
        with open(file_name, "r") as f:
            for line in f:
                if NTP_ETC_STORLEVER_CONF_FILE in line:
                    already_include = True
        if not already_include:
            with open(file_name, "a") as f:
                f.write("\nincludefile %s\n" % file_name)

    def sync_to_system_conf(self):
        """sync the ntp conf to /etc/ntp.conf"""

        if not os.path.exists(self.conf_file):
            return  # if not conf file, don't change the system config

        with self.lock:
            ntp_conf = self._load_conf()
            self._sync_to_system_conf(ntp_conf)

    def system_restore_cb(self):
        """sync the ntp conf to /etc/ntp"""

        if not os.path.exists(self.conf_file):
            return  # if not conf file, don't change the system config

        os.remove(self.conf_file)

        with self.lock:
            ntp_conf = self._load_conf()
            self._sync_to_system_conf(ntp_conf)

    def get_server_conf_list(self):

        with self.lock:
            ntp_conf = self._load_conf()
        return ntp_conf["server_list"]

    def get_server_conf(self, index):
        with self.lock:
            ntp_conf = self._load_conf()
            server_list = ntp_conf["server_list"]
        if index >= len(server_list):
            raise StorLeverError("index(%d) of server config not found" % (index), 404)
        return server_list[index]

    def append_server_conf(self, server_addr, ipv6=False, prefer=False,
                           mode=0, stratum=0, flag1=0, flag2=0, flag3=0, flag4=0,
                           operator="unkown"):
        new_server_conf = {
            "server_addr": server_addr,
            "ipv6": ipv6,
            "prefer": prefer,
            "mode": mode,
            "stratum": stratum,
            "flag1": flag1,
            "flag2": flag2,
            "flag3": flag3,
            "flag4": flag4,
        }
        new_server_conf = self.ntp_server_conf_schema.validate(new_server_conf)

        with self.lock:
            ntp_conf = self._load_conf()
            server_list = ntp_conf["server_list"]
            server_list.append(new_server_conf)

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "NTP Server (%s) config is added by operator(%s)" %
                   (server_addr, operator))
        return len(server_list) - 1

    def del_server_conf(self, index, operator="unkown"):
        with self.lock:
            ntp_conf = self._load_conf()
            server_list = ntp_conf["server_list"]
            if index >= len(server_list):
                raise StorLeverError("index(%d) of server config not found" % (index), 404)
            server_conf = server_list[index]
            del server_list[index]

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "NTP Server (%s) config is deleted by operator(%s)" %
                   (server_conf["server_addr"], operator))

    def set_server_conf(self, index, server_addr=None, ipv6=None, prefer=None,
                        mode=None, stratum=None,
                        flag1=None, flag2=None, flag3=None, flag4=None, operator="unkown"):
        with self.lock:
            ntp_conf = self._load_conf()
            server_list = ntp_conf["server_list"]
            if index >= len(server_list):
                raise StorLeverError("index(%d) of server config not found" % (index), 404)
            server_conf = server_list[index]
            if server_addr is not None:
                server_conf["server_addr"] = server_addr
            if ipv6 is not None:
                server_conf["ipv6"] = ipv6
            if prefer is not None:
                server_conf["prefer"] = prefer
            if mode is not None:
                server_conf["mode"] = mode
            if stratum is not None:
                server_conf["stratum"] = stratum
            if flag1 is not None:
                server_conf["flag1"] = flag1
            if flag2 is not None:
                server_conf["flag2"] = flag2
            if flag3 is not None:
                server_conf["flag3"] = flag3
            if flag4 is not None:
                server_conf["flag4"] = flag4

            server_list[index] = self.ntp_server_conf_schema.validate(server_conf)

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "NTP Server (%s) config is updated by operator(%s)" %
                   (server_list[index]["server_addr"], operator))

    def get_restrict_list(self):
        with self.lock:
            ntp_conf = self._load_conf()
        return ntp_conf["restrict_list"]

    def get_restrict(self, index):
        with self.lock:
            ntp_conf = self._load_conf()
            restrict_list = ntp_conf["restrict_list"]
        if index >= len(restrict_list):
            raise StorLeverError("index(%d) of restrict not found" % (index), 404)
        return restrict_list[index]

    def append_restrict(self, restrict_addr, ipv6=False, mask="",
                        ignore=False, nomodify=False, noquery=False,
                        noserve=False, notrap=False, operator="unkown"):

        new_restrict_conf = {
            "restrict_addr": restrict_addr,
            "ipv6": ipv6,
            "mask": mask,
            "ignore": ignore,
            "nomodify": nomodify,
            "noquery": noquery,
            "noserve": noserve,
            "notrap": notrap,
        }
        new_restrict_conf = self.ntp_restrict_conf_schema.validate(new_restrict_conf)

        with self.lock:
            ntp_conf = self._load_conf()
            restrict_list = ntp_conf["restrict_list"]
            restrict_list.append(new_restrict_conf)

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "Restrict (%s) config is added by operator(%s)" %
                   (restrict_addr, operator))
        return len(restrict_list) - 1

    def del_restrict(self, index, operator="unkown"):
        with self.lock:
            ntp_conf = self._load_conf()
            restrict_list = ntp_conf["restrict_list"]
            if index >= len(restrict_list):
                raise StorLeverError("index(%d) of restrict config not found" % (index), 404)
            restrict_conf = restrict_list[index]
            del restrict_list[index]

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "Restrict (%s) config is deleted by operator(%s)" %
                   (restrict_conf["restrict_addr"], operator))

    def set_restrict(self, index, restrict_addr=None, ipv6=None, mask=None,
                        ignore=None, nomodify=None, noquery=None,
                        noserve=None, notrap=None, operator="unkown"):
        with self.lock:
            ntp_conf = self._load_conf()
            restrict_list = ntp_conf["restrict_list"]
            if index >= len(restrict_list):
                raise StorLeverError("index(%d) of restrict config not found" % (index), 404)
            restrcit_conf = restrict_list[index]
            if restrict_addr is not None:
                restrcit_conf["restrict_addr"] = restrict_addr
            if ipv6 is not None:
                restrcit_conf["ipv6"] = ipv6
            if mask is not None:
                restrcit_conf["mask"] = mask
            if ignore is not None:
                restrcit_conf["ignore"] = ignore
            if nomodify is not None:
                restrcit_conf["nomodify"] = nomodify
            if noquery is not None:
                restrcit_conf["noquery"] = noquery
            if noserve is not None:
                restrcit_conf["noserve"] = noserve
            if notrap is not None:
                restrcit_conf["notrap"] = notrap

            restrict_list[index] = self.ntp_restrict_conf_schema.validate(restrcit_conf)

            # save new conf
            self._save_conf(ntp_conf)
            self._sync_to_system_conf(ntp_conf)

        logger.log(logging.INFO, logger.LOG_TYPE_CONFIG,
                   "NTP restrict (%s) config is updated by operator(%s)" %
                   (restrict_list[index]["restrict_addr"], operator))

    def get_ntp_connections(self):

        connections = []
        ll = check_output([NTPQ_CMD, '-pn']).splitlines()
        for l in ll[2:]:
            s = l[1:].split()
            if len(s) < 10:
                raise StorLeverError("ntpq output format cannot be regonized" ,
                                     500)
            connections.append({
                "state": l[0],
                "remote": s[0],
                "refid": s[1],
                "stratum": int(s[2]),
                "type": s[3],
                "when": float(s[4]),
                "poll": float(s[5]),
                "reach": float(s[6]),
                "delay": float(s[7]),
                "offset": float(s[8]),
                "jitter": float(s[9])
            })
        return connections

NtpManager = NtpManager()

# register ftp manager callback functions to basic manager
cfg_mgr().register_restore_from_file_cb(NtpManager.sync_to_system_conf)
cfg_mgr().register_system_restore_cb(NtpManager.system_restore_cb)
service_mgr().register_service("ntp", "ntpd", "ntpd", "NTP Server(ntpd)")


def ntp_mgr():
    """return the global user manager instance"""
    return NtpManager
