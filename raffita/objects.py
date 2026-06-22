#!/usr/bin/env python3
# Central registry of all network object types for the Raffita toolkit.
#
# Each entry in OBJECTS provides: schema, build, delete, show.
# Validation ranges are co-located with each schema field.

from .gen_lib import render_template, generate_vlan_name
import ipaddress


# ── Validators ────────────────────────────────────────────────────────────────

def _valid_ip(v):
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False

def _valid_mask(v):
    try:
        ipaddress.ip_network(f"0.0.0.0/{v}", strict=False)
        return True
    except ValueError:
        return False

def _valid_vlan(v):   return 1 <= v <= 4094
def _valid_isid(v):   return 256 <= v <= 16_777_214
def _valid_vrf_id(v): return 1 <= v <= 511
def _valid_vrrp_id(v):return 1 <= v <= 255
def _valid_priority(v):return 1 <= v <= 254
def _valid_mlt_id(v): return 1 <= v <= 512
def _valid_loopback_id(v): return 1 <= v <= 256


# ── Schemas ───────────────────────────────────────────────────────────────────

SCHEMA_ANYCAST = {
    "VLAN_ID":            {"type": int,  "required": True,
                           "help": "VLAN ID (1-4094)",
                           "validate": _valid_vlan,
                           "validate_msg": "VLAN_ID must be 1-4094."},
    "IP_ADDRESS":         {"type": str,  "required": True,
                           "help": "Anycast gateway IP address",
                           "validate": _valid_ip,
                           "validate_msg": "IP_ADDRESS is not a valid IP address."},
    "SUBNET_MASK":        {"type": str,  "required": True,
                           "help": "Subnet mask (dotted decimal)",
                           "validate": _valid_mask,
                           "validate_msg": "SUBNET_MASK is not a valid subnet mask."},
    "VRF_NAME":           {"type": str,  "required": True,  "help": "VRF name"},
    "I_SID":              {"type": int,  "required": True,
                           "help": "I-SID (256-16777214)",
                           "validate": _valid_isid,
                           "validate_msg": "I_SID must be between 256 and 16777214."},
    "HOSTNAME":           {"type": str,  "required": True,  "help": "Target switch"},
    "WAKE_ON_LAN":        {"type": bool, "required": False, "default": False, "help": "Wake-on-LAN / directed broadcast"},
    "DHCP_RELAY_ENABLED": {"type": bool, "required": False, "default": False, "help": "Enable DHCP relay"},
    "DHCP_RELAY_IP":      {"type": list, "required": False, "default": [],    "help": "DHCP relay IP (repeatable)"},
    "LAST_RELAY_IS_BOOTP":{"type": bool, "required": False, "default": True,  "help": "Treat last relay IP as BOOTP server"},
}

SCHEMA_DVR = {
    "VLAN_ID":            {"type": int,  "required": True,
                           "help": "VLAN ID (1-4094)",
                           "validate": _valid_vlan,
                           "validate_msg": "VLAN_ID must be 1-4094."},
    "IP_ADDRESS":         {"type": str,  "required": True,
                           "help": "DVR one-IP gateway address",
                           "validate": _valid_ip,
                           "validate_msg": "IP_ADDRESS is not a valid IP address."},
    "SUBNET_MASK":        {"type": str,  "required": True,
                           "help": "Subnet mask",
                           "validate": _valid_mask,
                           "validate_msg": "SUBNET_MASK is not a valid subnet mask."},
    "VRF_NAME":           {"type": str,  "required": True,  "help": "VRF name"},
    "I_SID":              {"type": int,  "required": True,
                           "help": "I-SID (256-16777214)",
                           "validate": _valid_isid,
                           "validate_msg": "I_SID must be between 256 and 16777214."},
    "HOSTNAME":           {"type": str,  "required": True,  "help": "Target switch"},
    "WAKE_ON_LAN":        {"type": bool, "required": False, "default": False, "help": "Wake-on-LAN"},
    "DHCP_RELAY_ENABLED": {"type": bool, "required": False, "default": False, "help": "Enable DHCP relay"},
    "DHCP_RELAY_IP":      {"type": list, "required": False, "default": [],    "help": "DHCP relay IP (repeatable)"},
    "LAST_RELAY_IS_BOOTP":{"type": bool, "required": False, "default": False, "help": "Last relay IP as BOOTP server"},
}

SCHEMA_RSMLT = {
    "VLAN_ID":            {"type": int,  "required": True,
                           "validate": _valid_vlan,
                           "validate_msg": "VLAN_ID must be 1-4094.",
                           "help": "VLAN ID"},
    "I_SID":              {"type": int,  "required": True,
                           "validate": _valid_isid,
                           "validate_msg": "I_SID must be 256-16777214.",
                           "help": "I-SID"},
    "IP_ADDRESS":         {"type": str,  "required": True,
                           "validate": _valid_ip,
                           "validate_msg": "IP_ADDRESS is not a valid IP address.",
                           "help": "VLAN interface IP address"},
    "SUBNET_MASK":        {"type": str,  "required": True,
                           "validate": _valid_mask,
                           "validate_msg": "SUBNET_MASK is not a valid subnet mask.",
                           "help": "Subnet mask"},
    "VRF_NAME":           {"type": str,  "required": True,  "help": "VRF name"},
    "WAKE_ON_LAN":        {"type": bool, "required": False, "default": False, "help": "Wake-on-LAN"},
    "HOSTNAME":           {"type": str,  "required": True,  "help": "Target switch"},
    "DHCP_RELAY_ENABLED": {"type": bool, "required": False, "default": False, "help": "Enable DHCP relay"},
    "DHCP_RELAY_IP":      {"type": list, "required": False, "default": [],    "help": "DHCP relay IP (repeatable)"},
    "LAST_RELAY_IS_BOOTP":{"type": bool, "required": False, "default": True,  "help": "Last relay IP as BOOTP server"},
}

SCHEMA_VRRP = {
    "HOSTNAME":           {"type": str,  "required": True,  "help": "Target switch"},
    "VLAN_ID":            {"type": int,  "required": True,
                           "validate": _valid_vlan, "validate_msg": "VLAN_ID must be 1-4094.",
                           "help": "VLAN ID"},
    "I_SID":              {"type": int,  "required": True,
                           "validate": _valid_isid, "validate_msg": "I_SID must be 256-16777214.",
                           "help": "I-SID"},
    "VRF_NAME":           {"type": str,  "required": True,  "help": "VRF name"},
    "IP_ADDRESS":         {"type": str,  "required": True,
                           "validate": _valid_ip, "validate_msg": "IP_ADDRESS is not a valid IP address.",
                           "help": "Interface IP address"},
    "SUBNET_MASK":        {"type": str,  "required": True,
                           "validate": _valid_mask, "validate_msg": "SUBNET_MASK is not a valid subnet mask.",
                           "help": "Subnet mask"},
    "VRRP_ID":            {"type": int,  "required": True,
                           "validate": _valid_vrrp_id, "validate_msg": "VRRP_ID must be 1-255.",
                           "help": "VRRP ID"},
    "VRRP_IP":            {"type": str,  "required": True,
                           "validate": _valid_ip, "validate_msg": "VRRP_IP is not a valid IP address.",
                           "help": "VRRP virtual IP address"},
    "PRIORITY":           {"type": int,  "required": True,
                           "validate": _valid_priority, "validate_msg": "PRIORITY must be 1-254.",
                           "help": "VRRP priority"},
    "BACKUP_MASTER":      {"type": bool, "required": False, "default": False, "help": "Enable backup master"},
    "DHCP_RELAY_ENABLED": {"type": bool, "required": False, "default": False, "help": "Enable DHCP relay"},
    "DHCP_RELAY_IP":      {"type": list, "required": False, "default": [],    "help": "DHCP relay IP (repeatable)"},
    "LAST_RELAY_IS_BOOTP":{"type": bool, "required": False, "default": True,  "help": "Last relay IP as BOOTP server"},
}

SCHEMA_LOOPBACK = {
    "LOOPBACK_INT_ID": {"type": int, "required": True,
                        "validate": _valid_loopback_id,
                        "validate_msg": "LOOPBACK_INT_ID must be 1-256.",
                        "help": "Loopback interface ID"},
    "IP_ADDRESS":      {"type": str, "required": True,
                        "validate": _valid_ip,
                        "validate_msg": "IP_ADDRESS is not a valid IP address.",
                        "help": "Loopback IP address"},
    "VRF_NAME":        {"type": str, "required": True, "help": "VRF name"},
    "HOSTNAME":        {"type": str, "required": True, "help": "Target switch"},
}

SCHEMA_MLT = {
    "MLT_ID":        {"type": int,  "required": True,
                      "validate": _valid_mlt_id, "validate_msg": "MLT_ID must be 1-512.",
                      "help": "MLT ID"},
    "MLT_NAME":      {"type": str,  "required": True,  "help": "MLT name"},
    "INTERFACE":     {"type": str,  "required": True,  "help": "Interface (e.g. 1/48)"},
    "SLPP_GUARD":    {"type": bool, "required": False, "default": False, "help": "SLPP Guard"},
    "BPDU_GUARD":    {"type": bool, "required": False, "default": False, "help": "BPDU Guard"},
    "LACP_ENABLED":  {"type": bool, "required": False, "default": False, "help": "Enable LACP"},
    "SMLT_ENABLED":  {"type": bool, "required": False, "default": False, "help": "Enable SMLT"},
    "VLACP_ENABLED": {"type": bool, "required": False, "default": False, "help": "Enable VLACP"},
    "LACP_KEY":      {"type": int,  "required": False, "default": None,  "help": "LACP key (required if LACP_ENABLED)"},
    "ISIS_ENABLED":  {"type": bool, "required": False, "default": False, "help": "ISIS SPBM on MLT"},
    "ISIS_METRIC":   {"type": int,  "required": False, "default": None,  "help": "ISIS metric (required if ISIS_ENABLED)"},
    "HOSTNAME":      {"type": str,  "required": True,  "help": "Target switch"},
}

SCHEMA_PORT = {
    "HOSTNAME":     {"type": str,  "required": True,  "help": "Target switch"},
    "INTERFACE":    {"type": str,  "required": True,  "help": "Interface (e.g. 1/48)"},
    "SLPP_GUARD":   {"type": bool, "required": False, "default": False, "help": "SLPP Guard"},
    "BPDU_GUARD":   {"type": bool, "required": False, "default": False, "help": "BPDU Guard"},
    "LLDP_DISABLE": {"type": bool, "required": False, "default": False, "help": "Disable LLDP Tx"},
    "VLACP":        {"type": bool, "required": False, "default": False, "help": "Enable VLACP"},
    "ISIS_ENABLED": {"type": bool, "required": False, "default": False, "help": "ISIS on interface"},
    "ISIS_METRIC":  {"type": int,  "required": False, "default": None,  "help": "ISIS metric (required if ISIS_ENABLED)"},
}

SCHEMA_ISID = {
    "HOSTNAME":    {"type": str,  "required": True,  "help": "Target switch"},
    "I_SID":       {"type": int,  "required": True,
                    "validate": _valid_isid, "validate_msg": "I_SID must be 256-16777214.",
                    "help": "I-SID"},
    "TAGGED":      {"type": bool, "required": False, "default": False, "help": "Tagged port?"},
    "PORT_TYPE":   {"type": str,  "required": True,  "help": "Port type (MLT or PORT)"},
    "MLT_PORT_ID": {"type": str,  "required": True,  "help": "MLT ID or interface (e.g. 1/3)"},
    "C_VID":       {"type": int,  "required": False, "default": None,
                    "validate": lambda v: 1 <= v <= 4094,
                    "validate_msg": "C_VID must be 1-4094.",
                    "help": "C-VID (required if TAGGED)"},
}

SCHEMA_ROUTE = {
    "HOSTNAME":    {"type": str, "required": True, "help": "Target switch"},
    "VRF_NAME":    {"type": str, "required": True, "help": "VRF name"},
    "IP_ADDRESS":  {"type": str, "required": True,
                    "validate": _valid_ip, "validate_msg": "IP_ADDRESS is not a valid IP address.",
                    "help": "Destination network"},
    "SUBNET_MASK": {"type": str, "required": True,
                    "validate": _valid_mask, "validate_msg": "SUBNET_MASK is not a valid subnet mask.",
                    "help": "Subnet mask"},
    "NEXT_HOP":    {"type": str, "required": True,
                    "validate": _valid_ip, "validate_msg": "NEXT_HOP is not a valid IP address.",
                    "help": "Next-hop IP address"},
}

SCHEMA_VRF = {
    "HOSTNAME":       {"type": str, "required": True, "help": "Target switch"},
    "VRF_NAME":       {"type": str, "required": True, "help": "VRF name"},
    "VRF_ID":         {"type": int, "required": True,
                       "validate": _valid_vrf_id, "validate_msg": "VRF_ID must be 1-511.",
                       "help": "VRF ID"},
    "L3_I_SID":       {"type": int, "required": True,
                       "validate": _valid_isid, "validate_msg": "L3_I_SID must be 256-16777214.",
                       "help": "L3 I-SID"},
    "VRF_MAX_ROUTES": {"type": int, "required": True,
                       "validate": lambda v: v >= 1,
                       "validate_msg": "VRF_MAX_ROUTES must be >= 1.",
                       "help": "Max routes in VRF"},
    "ECMP_MAX_PATH":  {"type": int, "required": True,
                       "validate": lambda v: 1 <= v <= 8,
                       "validate_msg": "ECMP_MAX_PATH must be 1-8.",
                       "help": "Max ECMP paths"},
}

SCHEMA_VRF_MULTIAREA = {
    "HOSTNAME": {"type": str, "required": True, "help": "Target switch"},
    "VRF_NAME": {"type": str, "required": True, "help": "VRF name"},
    "VRF_ID":   {"type": int, "required": True,
                 "validate": _valid_vrf_id, "validate_msg": "VRF_ID must be 1-511.",
                 "help": "VRF ID"},
    "L3_I_SID": {"type": int, "required": True,
                 "validate": _valid_isid, "validate_msg": "L3_I_SID must be 256-16777214.",
                 "help": "L3 I-SID"},
}

SCHEMA_CLUSTER = {
    "NICKNAME":       {"type": str,  "required": True,  "help": "SPBM nickname (e.g. 7.39.30)"},
    "IST_NETWORK":    {"type": str,  "required": True,
                       "validate": lambda v: _valid_ist_network(v),
                       "validate_msg": "IST_NETWORK must be a valid /30 network (e.g. 10.34.0.68/30).",
                       "help": "IST /30 network (e.g. 10.34.0.68/30)"},
    "NODENAME1":      {"type": str,  "required": True,  "help": "First cluster member"},
    "NODENAME2":      {"type": str,  "required": True,  "help": "Second cluster member"},
    "DVR_LEAF":       {"type": bool, "required": False, "default": False, "help": "Enable DVR leaf"},
    "DVR_DOMAIN":     {"type": int,  "required": False, "default": None,  "help": "DVR domain (required if DVR_LEAF)"},
    "DVR_CLUSTER_ID": {"type": int,  "required": False, "default": None,  "help": "DVR cluster ID (required if DVR_LEAF)"},
}

def _valid_ist_network(v):
    try:
        net = ipaddress.ip_network(v, strict=False)
        return net.prefixlen == 30
    except ValueError:
        return False


# ── Builders ──────────────────────────────────────────────────────────────────

def _relay_ips(p):
    return p["DHCP_RELAY_IP"] if p.get("DHCP_RELAY_ENABLED") else []

def build_anycast(p):
    return render_template("anycast_one_ip_template.j2", {
        "VLAN_ID": p["VLAN_ID"],
        "VLAN_NAME": generate_vlan_name(p["IP_ADDRESS"], p["SUBNET_MASK"], prefix="C"),
        "I_SID": p["I_SID"], "VRF_NAME": p["VRF_NAME"],
        "IP_ADDRESS": p["IP_ADDRESS"], "SUBNET_MASK": p["SUBNET_MASK"],
        "WAKE_ON_LAN": p["WAKE_ON_LAN"],
        "DHCP_RELAY_ENABLED": p["DHCP_RELAY_ENABLED"],
        "RELAY_IPS": _relay_ips(p),
        "LAST_RELAY_IS_BOOTP": p["LAST_RELAY_IS_BOOTP"],
    })

def build_dvr(p):
    return render_template("dvr_one_ip_template.j2", {
        "VLAN_ID": p["VLAN_ID"],
        "VLAN_NAME": generate_vlan_name(p["IP_ADDRESS"], p["SUBNET_MASK"], prefix="C"),
        "I_SID": p["I_SID"], "VRF_NAME": p["VRF_NAME"],
        "IP_ADDRESS": p["IP_ADDRESS"], "SUBNET_MASK": p["SUBNET_MASK"],
        "WAKE_ON_LAN": p["WAKE_ON_LAN"],
        "DHCP_RELAY_ENABLED": p["DHCP_RELAY_ENABLED"],
        "RELAY_IPS": _relay_ips(p),
        "LAST_RELAY_IS_BOOTP": p["LAST_RELAY_IS_BOOTP"],
    })

def build_rsmlt(p):
    return render_template("rsmlt_template.j2", {
        "VLAN_ID": p["VLAN_ID"],
        "VLAN_NAME": generate_vlan_name(p["IP_ADDRESS"], p["SUBNET_MASK"], prefix="C"),
        "I_SID": p["I_SID"], "VRF_NAME": p["VRF_NAME"],
        "IP_ADDRESS": p["IP_ADDRESS"], "SUBNET_MASK": p["SUBNET_MASK"],
        "WAKE_ON_LAN": p["WAKE_ON_LAN"],
        "RSMLT_HOLDUP_TIMER": 9999,
        "DHCP_RELAY_ENABLED": p["DHCP_RELAY_ENABLED"],
        "RELAY_IPS": _relay_ips(p),
        "LAST_RELAY_IS_BOOTP": p["LAST_RELAY_IS_BOOTP"],
    })

def build_vrrp(p):
    return render_template("vrrp_config_template.j2", {
        "VLAN_ID": p["VLAN_ID"],
        "VLAN_NAME": generate_vlan_name(p["IP_ADDRESS"], p["SUBNET_MASK"], prefix="E"),
        "I_SID": p["I_SID"], "VRF_NAME": p["VRF_NAME"],
        "IP_ADDRESS": p["IP_ADDRESS"], "SUBNET_MASK": p["SUBNET_MASK"],
        "VRRP_ID": p["VRRP_ID"], "VRRP_IP": p["VRRP_IP"],
        "ADVER_INT": 10, "HOLDDOWN_TIMER": 60,
        "PRIORITY": p["PRIORITY"],
        "BACKUP_MASTER": "enable" if p["BACKUP_MASTER"] else None,
        "DHCP_RELAY_ENABLED": p["DHCP_RELAY_ENABLED"],
        "RELAY_IPS": _relay_ips(p),
        "LAST_RELAY_IS_BOOTP": p["LAST_RELAY_IS_BOOTP"],
    })

def build_loopback(p):
    return render_template("loopback_template.j2", {
        "LOOPBACK_INT_ID": p["LOOPBACK_INT_ID"],
        "IP_ADDRESS": p["IP_ADDRESS"],
        "VRF_NAME": p["VRF_NAME"],
    })

def build_mlt(p):
    if p["LACP_ENABLED"] and p["LACP_KEY"] is None:
        raise ValueError("--LACP_KEY is required when LACP_ENABLED is set.")
    if p["ISIS_ENABLED"] and p["ISIS_METRIC"] is None:
        raise ValueError("--ISIS_METRIC is required when ISIS_ENABLED is set.")
    return render_template("mlt_template.j2", {
        "MLT_ID": p["MLT_ID"], "MLT_NAME": p["MLT_NAME"],
        "INTERFACE": p["INTERFACE"],
        "SLPP_GUARD": p["SLPP_GUARD"], "BPDU_GUARD": p["BPDU_GUARD"],
        "LACP_ENABLED": p["LACP_ENABLED"], "SMLT_ENABLED": p["SMLT_ENABLED"],
        "VLACP_ENABLED": p["VLACP_ENABLED"], "LACP_KEY": p["LACP_KEY"],
        "ISIS_ENABLED": p["ISIS_ENABLED"], "ISIS_METRIC": p["ISIS_METRIC"],
    })

def build_port(p):
    if p["ISIS_ENABLED"] and p["ISIS_METRIC"] is None:
        raise ValueError("--ISIS_METRIC is required when ISIS_ENABLED is set.")
    return render_template("single_port_template.j2", {
        "INTERFACE": p["INTERFACE"],
        "SLPP_GUARD": p["SLPP_GUARD"], "BPDU_GUARD": p["BPDU_GUARD"],
        "VLACP": p["VLACP"], "LLDP_DISABLE": p["LLDP_DISABLE"],
        "ISIS_ENABLED": p["ISIS_ENABLED"], "ISIS_METRIC": p["ISIS_METRIC"],
    })

def build_isid(p):
    if p["TAGGED"] and p["C_VID"] is None:
        raise ValueError("--C_VID is required when TAGGED is set.")
    return render_template("i_sid_template.j2", {
        "I_SID": p["I_SID"], "TAGGED": p["TAGGED"],
        "PORT_TYPE": p["PORT_TYPE"].upper(),
        "MLT_PORT_ID": p["MLT_PORT_ID"], "C_VID": p["C_VID"],
    })

def build_route(p):
    return render_template("static_route_template.j2", {
        "VRF_NAME": p["VRF_NAME"],
        "IP_ADDRESS": p["IP_ADDRESS"], "SUBNET_MASK": p["SUBNET_MASK"],
        "NEXT_HOP": p["NEXT_HOP"],
    })

def build_vrf(p):
    return render_template("vrf_config_template.j2", {
        "VRF_NAME": p["VRF_NAME"], "VRF_ID": p["VRF_ID"],
        "L3_I_SID": p["L3_I_SID"],
        "VRF_MAX_ROUTES": p["VRF_MAX_ROUTES"], "ECMP_MAX_PATH": p["ECMP_MAX_PATH"],
    })

def build_vrf_multiarea(p):
    return render_template("vrf_multiarea_config_template.j2", {
        "VRF_NAME": p["VRF_NAME"], "VRF_ID": p["VRF_ID"],
        "L3_I_SID": p["L3_I_SID"],
    })


# ── Cluster builder (two nodes) ───────────────────────────────────────────────

def _second_nickname(nickname):
    parts = nickname.split(".")
    parts[-1] = "{:x}".format(int(parts[-1], 16) + 15)
    return ".".join(parts)

def _smlt_peer_sys_id(nickname):
    parts = nickname.split(".")
    fp = [part.zfill(2) for part in parts]
    return "0200.{0}{1}.{2}00".format(fp[0], fp[1], fp[2])

def _smlt_virtual_bmac(nickname):
    parts = nickname.split(".")
    fp = [f"{int(part):02d}" for part in parts]
    return "02:00:{0}:{1}:{2}:01".format(fp[0], fp[1], fp[2])

def _ist_ips(network):
    net = ipaddress.ip_network(network, strict=False)
    if net.prefixlen != 30:
        raise ValueError(f"'{network}' is not a valid /30 network.")
    return str(net[1]), str(net[2])

def _cluster_vlan_name(ist_network):
    net    = ipaddress.ip_network(ist_network, strict=False)
    hex_ip = "".join(f"{int(o):02x}" for o in str(net.network_address).split("."))
    return f"E{hex_ip}_30"

def _cluster_i_sid(nickname):
    return int("1{0}0".format(nickname.replace(".", "")))

def build_cluster(p):
    if p["DVR_LEAF"] and (p["DVR_DOMAIN"] is None or p["DVR_CLUSTER_ID"] is None):
        raise ValueError("--DVR_DOMAIN and --DVR_CLUSTER_ID are required when DVR_LEAF is set.")
    nickname      = p["NICKNAME"]
    nickname_peer = _second_nickname(nickname)
    ist_ip1, ist_ip2 = _ist_ips(p["IST_NETWORK"])
    smlt_sys_id  = _smlt_virtual_bmac(nickname)
    vlan_name    = _cluster_vlan_name(p["IST_NETWORK"])
    i_sid        = _cluster_i_sid(nickname)
    base = {
        "DVR_LEAF": p["DVR_LEAF"], "DVR_DOMAIN": p["DVR_DOMAIN"],
        "DVR_CLUSTER_ID": p["DVR_CLUSTER_ID"],
        "VLAN_NAME": vlan_name, "VIST_I_SID": i_sid, "SMLT_SYS_ID": smlt_sys_id,
    }
    p1 = dict(base, NICKNAME=nickname, NICKNAME_PEER=nickname_peer,
              IST_IP=ist_ip1, PEER_IP=ist_ip2, NODE_NAME=p["NODENAME1"],
              SMLT_PEER_SYS_ID=_smlt_peer_sys_id(nickname_peer))
    p2 = dict(base, NICKNAME=nickname_peer, NICKNAME_PEER=nickname,
              IST_IP=ist_ip2, PEER_IP=ist_ip1, NODE_NAME=p["NODENAME2"],
              SMLT_PEER_SYS_ID=_smlt_peer_sys_id(nickname))
    cfg1 = render_template("cluster_template.j2", p1)
    cfg2 = render_template("cluster_template.j2", p2)
    return [(p["NODENAME1"], cfg1), (p["NODENAME2"], cfg2)]


# ── Delete commands ───────────────────────────────────────────────────────────

_CONF_HDR = "enable\nconf t\nterm more disable\n"

def delete_vlan(p):
    return _CONF_HDR + f"vlan delete {p['VLAN_ID']}\n"

def delete_rsmlt(p):
    return (
        _CONF_HDR
        + f"no slpp vid {p['VLAN_ID']}\n"
        + f"vlan delete {p['VLAN_ID']}\n"
        + f"no i-sid {p['I_SID']}\n"
    )

def delete_loopback(p):
    return _CONF_HDR + f"no interface loopback {p['LOOPBACK_INT_ID']}\n"

def delete_mlt(p):
    out = _CONF_HDR + f"no mlt {p['MLT_ID']}\n"
    if p.get("INTERFACE"):
        out += f"default interface gigabitEthernet {p['INTERFACE']}\n"
    return out

def delete_isid(p):
    return _CONF_HDR + f"no i-sid {p['I_SID']}\n"

def delete_port(p):
    return _CONF_HDR + f"default interface gigabitEthernet {p['INTERFACE']}\n"

def delete_route(p):
    return (
        _CONF_HDR
        + f"router vrf {p['VRF_NAME']}\n"
        + f"  no ip route {p['IP_ADDRESS']} {p['SUBNET_MASK']} {p['NEXT_HOP']}\n"
        + "exit\n"
    )

def delete_vrf(p):
    return _CONF_HDR + f"no ip vrf {p['VRF_NAME']}\n"


# ── Show commands ─────────────────────────────────────────────────────────────

def show_vlan_l3(opts):
    vid = opts.get("VLAN_ID")
    return ([f"show vlan basic {vid}", "show interface vlan ip"]
            if vid else ["show vlan i-sid", "show interface vlan ip"])

def show_vrrp(opts):
    vid = opts.get("VLAN_ID")
    return ([f"show ip vrrp interface vlan {vid}", f"show vlan basic {vid}"]
            if vid else ["show ip vrrp", "show vlan i-sid"])

def show_loopback(opts):
    vrf = opts.get("VRF_NAME")
    return ([f"show ip interface vrf {vrf}"]
            if vrf else ["show interface loopback", "show ip interface"])

def show_mlt(opts):
    mid = opts.get("MLT_ID")
    return [f"show mlt {mid}"] if mid else ["show mlt"]

def show_isid(opts):
    isid = opts.get("I_SID")
    return [f"show i-sid {isid}"] if isid else ["show i-sid"]

def show_port(opts):
    intf = opts.get("INTERFACE")
    return ([f"show interfaces gigabitEthernet interface {intf}"]
            if intf else ["show interfaces gigabitEthernet"])

def show_route(opts):
    vrf = opts.get("VRF_NAME")
    return [f"show ip route vrf {vrf}"] if vrf else ["show ip route"]

def show_vrf(opts):
    vrf = opts.get("VRF_NAME")
    return [f"show ip vrf {vrf}"] if vrf else ["show ip vrf"]

def show_cluster(_opts):
    return ["show virtual-ist", "show smlt", "show isis spbm"]


# ── Registry ──────────────────────────────────────────────────────────────────

OBJECTS = {
    "anycast": {
        "desc": "Anycast-Gateway (one-ip) L3 VLAN interface",
        "schema": SCHEMA_ANYCAST, "build": build_anycast,
        "delete": delete_vlan,    "delete_params": ["VLAN_ID", "HOSTNAME"],
        "show": show_vlan_l3,     "show_params":   ["VLAN_ID"],
    },
    "dvr": {
        "desc": "DVR one-IP gateway L3 VLAN interface",
        "schema": SCHEMA_DVR,  "build": build_dvr,
        "delete": delete_vlan, "delete_params": ["VLAN_ID", "HOSTNAME"],
        "show": show_vlan_l3,  "show_params":   ["VLAN_ID"],
    },
    "rsmlt": {
        "desc": "RSMLT L3 VLAN interface",
        "schema": SCHEMA_RSMLT, "build": build_rsmlt,
        "delete": delete_rsmlt, "delete_params": ["VLAN_ID", "I_SID", "HOSTNAME"],
        "show": show_vlan_l3,   "show_params":   ["VLAN_ID"],
    },
    "vrrp": {
        "desc": "VRRP L3 VLAN interface",
        "schema": SCHEMA_VRRP, "build": build_vrrp,
        "delete": delete_vlan, "delete_params": ["VLAN_ID", "HOSTNAME"],
        "show": show_vrrp,     "show_params":   ["VLAN_ID"],
    },
    "loopback": {
        "desc": "Circuitless IP / loopback interface",
        "schema": SCHEMA_LOOPBACK, "build": build_loopback,
        "delete": delete_loopback, "delete_params": ["LOOPBACK_INT_ID", "HOSTNAME"],
        "show": show_loopback,     "show_params":   ["VRF_NAME"],
    },
    "mlt": {
        "desc": "MultiLink Trunk + interface",
        "schema": SCHEMA_MLT, "build": build_mlt,
        "delete": delete_mlt, "delete_params": ["MLT_ID", "INTERFACE", "HOSTNAME"],
        "delete_optional": ["INTERFACE"],
        "show": show_mlt,     "show_params":   ["MLT_ID"],
    },
    "port": {
        "desc": "Single interface",
        "schema": SCHEMA_PORT, "build": build_port,
        "delete": delete_port, "delete_params": ["INTERFACE", "HOSTNAME"],
        "show": show_port,     "show_params":   ["INTERFACE"],
    },
    "isid": {
        "desc": "I-SID on port/MLT (L2 service)",
        "schema": SCHEMA_ISID, "build": build_isid,
        "delete": delete_isid, "delete_params": ["I_SID", "HOSTNAME"],
        "show": show_isid,     "show_params":   ["I_SID"],
    },
    "route": {
        "desc": "Static route in VRF",
        "schema": SCHEMA_ROUTE, "build": build_route,
        "delete": delete_route,
        "delete_params": ["VRF_NAME", "IP_ADDRESS", "SUBNET_MASK", "NEXT_HOP", "HOSTNAME"],
        "show": show_route, "show_params": ["VRF_NAME"],
    },
    "vrf": {
        "desc": "VRF with IPVPN",
        "schema": SCHEMA_VRF, "build": build_vrf,
        "delete": delete_vrf, "delete_params": ["VRF_NAME", "HOSTNAME"],
        "show": show_vrf,     "show_params":   ["VRF_NAME"],
    },
    "vrf_multiarea": {
        "desc": "VRF multi-area redistribution",
        "schema": SCHEMA_VRF_MULTIAREA, "build": build_vrf_multiarea,
        "delete": delete_vrf,           "delete_params": ["VRF_NAME", "HOSTNAME"],
        "show": show_vrf,               "show_params":   ["VRF_NAME"],
    },
    "cluster": {
        "desc": "vIST cluster (generates config for two nodes)",
        "schema": SCHEMA_CLUSTER, "build": build_cluster, "multi_host": True,
        "delete": None,           "delete_params": [],
        "show": show_cluster,     "show_params":   [],
    },
}
