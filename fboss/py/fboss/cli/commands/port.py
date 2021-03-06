#!/usr/bin/env python3
#
#  Copyright (c) 2004-present, Facebook, Inc.
#  All rights reserved.
#
#  This source code is licensed under the BSD-style license found in the
#  LICENSE file in the root directory of this source tree. An additional grant
#  of patent rights can be found in the PATENTS file in the same directory.
#

import collections
import time

from fboss.cli.utils import utils
from fboss.cli.commands import commands as cmds
from math import log10
from neteng.fboss.transceiver import ttypes as transceiver_ttypes
from neteng.fboss.ttypes import FbossBaseError
from thrift.Thrift import TApplicationException


class PortDetailsCmd(cmds.FbossCmd):
    def run(self, ports):
        try:
            self._client = self._create_agent_client()
            # No ports specified, get all ports
            if not ports:
                resp = self._client.getAllPortInfo()

        except FbossBaseError as e:
            raise SystemExit('Fboss Error: ' + e)

        except Exception as e:
            raise Exception('Error: ' + e)

        if ports:
            for port in ports:
                self._print_port_details(port)
        elif resp:
            all_ports = sorted(resp.values(), key=utils.port_sort_fn)
            all_ports = [port for port in all_ports if port.operState == 1]
            for port in all_ports:
                self._print_port_details(port.portId, port)
        else:
            print("No Ports Found")

    def _convert_bps(self, bps):
        ''' convert bps to human readable form

            :var bps int: port speed in bps
            :return bps_per_unit float: bps divided by factor of the unit found
            :return suffix string: human readable format
        '''

        bps_per_unit = suffix = None
        value = bps
        # expand to 'T' and beyond by adding in the proper unit
        for factor, unit in [(1, ''), (3, 'K'), (6, 'M'), (9, 'G')]:
            if value < 1000:
                bps_per_unit = bps / 10 ** factor
                suffix = '{}bps'.format(unit)
                break
            value /= 1000

        assert bps_per_unit is not None and suffix, (
            'Unable to convert bps to human readable format')

        return bps_per_unit, suffix

    def _print_port_details(self, port_id, port_info=None):
        ''' Print out port details

            :var port_id int: port identifier
            :var port_info PortInfoThrift: port information
        '''

        if not port_info:
            port_info = self._client.getPortInfo(port_id)

        admin_status = "ENABLED" if port_info.adminState else "DISABLED"
        oper_status = "UP" if port_info.operState else "DOWN"

        speed, suffix = self._convert_bps(port_info.speedMbps * (10 ** 6))
        vlans = ' '.join(str(vlan) for vlan in (port_info.vlans or []))

        if not hasattr(port_info, 'fecEnabled'):
            fec_status = "N/A"  # many ports don't implement FEC
        elif port_info.fecEnabled:
            fec_status = "ENABLED"
        else:
            fec_status = "DISABLED"

        fmt = '{:.<50}{}'
        lines = [
            ('Name', port_info.name.strip()),
            ('Port ID', str(port_info.portId)),
            ('Admin State', admin_status),
            ('Link State', oper_status),
            ('Speed', '{:.0f} {}'.format(speed, suffix)),
            ('VLANs', vlans),
            ('Forward Error Correction', fec_status),
        ]

        print()
        print('\n'.join(fmt.format(*l) for l in lines))
        print('Description'.ljust(20, '.') + (port_info.description or ""))


class PortFlapCmd(cmds.FbossCmd):
    def run(self, ports):
        try:
            if not ports:
                print("Hmm, how did we get here?")
            else:
                self.flap_ports(ports)
        except FbossBaseError as e:
            raise SystemExit('Fboss Error: ' + e)

    def flap_ports(self, ports):
        self._client = self._create_agent_client()
        resp = self._client.getPortStatus(ports)
        for port, status in resp.items():
            if not status.enabled:
                print("Port %d is disabled by configuration, cannot flap" %
                      (port))
                continue
            print("Disabling port %d" % (port))
            self._client.setPortState(port, False)
        time.sleep(1)
        for port, status in resp.items():
            if status.enabled:
                print("Enabling port %d" % (port))
                self._client.setPortState(port, True)


class PortSetStatusCmd(cmds.FbossCmd):
    def run(self, ports, status):
        try:
            self.set_status(ports, status)
        except FbossBaseError as e:
            raise SystemExit('Fboss Error: ' + e)

    def set_status(self, ports, status):
        self._client = self._create_agent_client()
        for port in ports:
            status_str = 'Enabling' if status else 'Disabling'
            print("{} port {}".format(status_str, port))
            self._client.setPortState(port, status)


class PortStatsCmd(cmds.FbossCmd):
    def run(self, details, ports):
        try:
            self.show_stats(details, ports)
        except FbossBaseError as e:
            raise SystemExit('Fboss Error: ' + e)

    def show_stats(self, details, ports):
        with self._create_ctrl_client() as client:
            if not ports:
                stats = client.getAllPortStats()
            else:
                stats = {}
                for port in ports:
                    stats[port] = client.getPortStats(port)
            neighbors = client.getLldpNeighbors()
        n2ports = {}
        # collect up the neighbors by port
        for neighbor in neighbors:
            n2ports.setdefault(neighbor.localPort, []).append(neighbor)
        # Port Name
        field_fmt = '{:<11} {:>3} {:>} {:<} {:>} {:<} {:<}'
        hosts = "Hosts" if details else ""

        print(field_fmt.format("Port Name", "+Id", "In",
                               self._get_counter_string(None),
                               "Out", self._get_counter_string(None), hosts))
        for port_id, port in stats.items():
            print(field_fmt.format(port.name, port_id, "In",
                                   self._get_counter_string(port.input),
                                   "Out", self._get_counter_string(port.output),
                                   self._get_lldp_string(port_id, n2ports, details)))

    def _get_counter_string(self, counters):
        # bytes uPts mPts bPts err disc
        field_fmt = '{:>15} {:>15} {:>10} {:>10} {:>10} {:>10}'
        if counters is None:
            return field_fmt.format("bytes", "uPkts", "mcPkts", "bcPkts",
                                    "errs", "disc")
        else:
            return field_fmt.format(counters.bytes, counters.ucastPkts,
                                    counters.multicastPkts,
                                    counters.broadcastPkts,
                                    counters.errors.errors,
                                    counters.errors.discards)

    def _get_lldp_string(self, port_id, n2ports, details):
        ret = ""
        if details and port_id in n2ports:
            for n in n2ports[port_id]:
                ret += " {}".format(n.systemName)
        return ret


class PortStatusCmd(cmds.FbossCmd):
    def run(self, detail, ports, verbose, internal):
        self._client = self._create_agent_client()
        self._qsfp_client = self._create_qsfp_client()
        if detail or verbose:
            PortStatusDetailCmd(
                self._client, ports, self._qsfp_client, verbose
            ).get_detail_status()
        elif internal:
            self.list_ports(ports, internal_port=True)
        else:
            self.list_ports(ports)

    def _get_field_format(self, internal_port):
        if internal_port:
            field_fmt = '{:>6} {:<11} {:>12}  {}{:>10}  {:>12}  {:>6}'
            print(field_fmt.format('Port ID', 'Port Name', 'Admin State', '',
                                   'Link State', 'Transceiver', 'Speed'))
            print('-' * 68)
        else:
            field_fmt = '{:<11} {:>12}  {}{:>10}  {:>12}  {:>6}'
            print(field_fmt.format('Port', 'Admin State', '', 'Link State',
                                   'Transceiver', 'Speed'))
            print('-' * 59)
        return field_fmt

    def list_ports(self, ports, internal_port=False):
        field_fmt = self._get_field_format(internal_port)
        port_status_map = self._client.getPortStatus(ports)
        qsfp_info_map = utils.get_qsfp_info_map(
            self._qsfp_client, None, continue_on_error=True)
        port_info_map = self._client.getAllPortInfo()
        missing_port_status = []
        for port_info in sorted(port_info_map.values(), key=utils.port_sort_fn):
            port_id = port_info.portId
            if ports and (port_id not in ports):
                continue
            status = port_status_map.get(port_id)
            if not status:
                missing_port_status.append(port_id)
                continue
            # The transceiver id can be derived from port name
            # e.g. port name eth1/4/1 -> transceiver_id is 4-1 = 3
            # (-1 because we start counting transceivers at 0)
            transceiver_id = utils.port_sort_fn(port_info)[2] - 1
            qsfp_info = qsfp_info_map.get(transceiver_id)
            if qsfp_info:
                qsfp_present = qsfp_info.present
            else:
                qsfp_present = False
            attrs = utils.get_status_strs(status, qsfp_present)
            if internal_port:
                speed = attrs['speed']
                if not speed:
                    speed = '-'
                print(field_fmt.format(
                    port_id,
                    port_info.name,
                    attrs['admin_status'],
                    attrs['color_align'],
                    attrs['link_status'],
                    attrs['present'],
                    speed))
            elif status.enabled:
                name = port_info.name if port_info.name else port_id
                print(field_fmt.format(
                    name,
                    attrs['admin_status'],
                    attrs['color_align'],
                    attrs['link_status'],
                    attrs['present'],
                    attrs['speed']))
        if missing_port_status:
            print(utils.make_error_string(
                "Could not get status of ports {}".format(missing_port_status)))



class PortStatusDetailCmd(object):
    ''' Print detailed/verbose port status '''

    def __init__(self, client, ports, qsfp_client, verbose):
        self._client = client
        self._qsfp_client = qsfp_client
        self._ports = ports
        self._port_speeds = self._get_port_speeds()
        self._info_resp = None
        self._status_resp = self._client.getPortStatus(ports)
        # map of { transceiver_id -> { channel_id -> port } }
        self._t_to_p = collections.defaultdict(dict)
        self._transceiver = []
        self._verbose = verbose

    def _get_port_speeds(self):
        ''' Get speeds for all ports '''

        all_info = self._client.getAllPortInfo()
        return dict((p, info.speedMbps) for p, info in all_info.items())

    def _get_port_channels(self, port, xcvr_info):
        '''  This function handles figuring out correct channel info even for
             older controllers that don't return full channel info. '''

        start_channel = xcvr_info.channelId
        speed = self._port_speeds[port]

        # speed == 1000 and N/A are one channel
        channels = [start_channel]
        if speed == 20000:
            channels = range(start_channel, start_channel + 2)
        elif speed == 40000:
            channels = range(start_channel, start_channel + 4)

        return channels

    def _get_channel_detail(self, port, status):
        ''' Get channel detail for port '''

        channels = status.transceiverIdx.channels
        if not channels:
            channels = self._get_port_channels(
                port, status.transceiverIdx)

        tid = status.transceiverIdx.transceiverId
        for ch in channels:
            self._t_to_p[tid][ch] = port

        if tid not in self._transceiver:
            self._transceiver.append(tid)

    def _mw_to_dbm(self, mw):
        if mw == 0:
            return 0.0
        else:
            return (10 * log10(mw))

    def _get_dummy_status(self):
        ''' Get dummy status for ports without data '''

        for port, status in sorted(self._status_resp.items()):
            if status.transceiverIdx:
                tid = status.transceiverIdx.transceiverId
                if tid not in self._info_resp.keys():
                    info = transceiver_ttypes.TransceiverInfo()
                    info.port = port
                    info.present = False
                    self._info_resp[port] = info

    def _print_transceiver_ports(self, ch_to_port, info):
        # Print port info if the transceiver doesn't have any
        for port in ch_to_port.values():
            attrs = utils.get_status_strs(self._status_resp[port],
                                          info.present)
            print("Port: {:>2}  Status: {:<8}  Link: {:<4}  Transceiver: {}".
                    format(port, attrs['admin_status'], attrs['link_status'],
                            attrs['present']))

    def _print_vendor_details(self, info):
        ''' print vendor details '''

        print("Vendor:  {:<16}  Part Number:  {:<16}".format(
              info.vendor.name, info.vendor.partNumber))
        print("Serial:  {:<16}  ".format(info.vendor.serialNumber), end="")
        print("Date Code:  {:<8}  Revision: {:<2}".format(
              info.vendor.dateCode, info.vendor.rev))

    def _print_settings_details(self, info):
        ''' print setting details'''
        print("CDR Tx: {}\tCDR Rx: {}".format(
            transceiver_ttypes.FeatureState._VALUES_TO_NAMES[
                info.settings.cdrTx],
            transceiver_ttypes.FeatureState._VALUES_TO_NAMES[
                info.settings.cdrRx]))
        print("Rate select: {}".format(
              transceiver_ttypes.RateSelectState._VALUES_TO_NAMES[
                  info.settings.rateSelect]))
        print("\tOptimised for: {}".format(
              transceiver_ttypes.RateSelectSetting._VALUES_TO_NAMES[
                  info.settings.rateSelectSetting]))
        print("Power measurement: {}".format(
            transceiver_ttypes.FeatureState._VALUES_TO_NAMES[
                info.settings.powerMeasurement]))
        print("Power control: {}".format(
            transceiver_ttypes.PowerControlState._VALUES_TO_NAMES[
                info.settings.powerControl]))

    def _print_cable_details(self, info):
        ''' print cable details '''

        print("Cable:", end=""),
        if info.cable.singleModeKm:
            print("Single Mode:  {}km".format(
                  info.cable.singleModeKm % 1000), end=""),
        if info.cable.singleMode:
            print("Single Mode:  {}m".format(
                  info.cable.singleMode), end=""),
        if info.cable.om3:
            print("OM3:  {}m".format(info.cable.om3), end=""),
        if info.cable.om2:
            print("OM2:  {}m".format(info.cable.om2), end=""),
        if info.cable.om1:
            print("OM1:  {}m".format(info.cable.om1), end=""),
        if info.cable.copper:
            print("Copper:  {}m".format(info.cable.copper), end="")
        print("")

    def _print_thresholds(self, thresh):
        ''' print threshold details '''

        print("  {:<16}   {:>10} {:>15} {:>15} {:>10}".format(
            'Thresholds:', 'Alarm Low', 'Warning Low', 'Warning High',
            'Alarm High'))
        print("    {:<14} {:>9.4}C {:>14.4}C {:>14.4}C {:>9.4}C".format(
            'Temp:',
            thresh.temp.alarm.low, thresh.temp.warn.low,
            thresh.temp.warn.high, thresh.temp.alarm.high))
        print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
            'Vcc:',
            thresh.vcc.alarm.low, thresh.vcc.warn.low,
            thresh.vcc.warn.high, thresh.vcc.alarm.high))
        print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
            'Tx Bias:',
            thresh.txBias.alarm.low, thresh.txBias.warn.low,
            thresh.txBias.warn.high, thresh.txBias.alarm.high))
        if thresh.txPwr:
            print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
                'Tx Power(dBm):',
                self._mw_to_dbm(thresh.txPwr.alarm.low),
                self._mw_to_dbm(thresh.txPwr.warn.low),
                self._mw_to_dbm(thresh.txPwr.warn.high),
                self._mw_to_dbm(thresh.txPwr.alarm.high)))
            print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
                'Tx Power(mW):',
                thresh.txPwr.alarm.low,
                thresh.txPwr.warn.low,
                thresh.txPwr.warn.high,
                thresh.txPwr.alarm.high))
        print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
            'Rx Power(dBm):',
            self._mw_to_dbm(thresh.rxPwr.alarm.low),
            self._mw_to_dbm(thresh.rxPwr.warn.low),
            self._mw_to_dbm(thresh.rxPwr.warn.high),
            self._mw_to_dbm(thresh.rxPwr.alarm.high)))
        print("    {:<14} {:>10.4} {:>15.4} {:>15.4} {:>10.4}".format(
            'Rx Power(mW):',
            thresh.rxPwr.alarm.low,
            thresh.rxPwr.warn.low,
            thresh.rxPwr.warn.high,
            thresh.rxPwr.alarm.high))

    def _print_sensor_flags(self, sensor):
        ''' print details about sensor flags '''

        # header
        print("  {:<12}   {:>10} {:>15} {:>15} {:>10}".format(
            'Flags:', 'Alarm Low', 'Warning Low', 'Warning High', 'Alarm High'))

        sensor_tmpl = "    {:<12} {:>10} {:>15} {:>15} {:>10}"
        # temp
        print(sensor_tmpl.format('Temp:',
              sensor.temp.flags.alarm.low,
              sensor.temp.flags.warn.low,
              sensor.temp.flags.warn.high,
              sensor.temp.flags.alarm.high))

        # vcc
        print(sensor_tmpl.format('Vcc:',
              sensor.vcc.flags.alarm.low,
              sensor.vcc.flags.warn.low,
              sensor.vcc.flags.warn.high,
              sensor.vcc.flags.alarm.high))

    def _print_port_channel(self, channel):
        # per-channel output:
        print("  {:<15} {:0.4}  ".format("Tx Bias(mA)",
              channel.sensors.txBias.value), end="")
        if channel.sensors.txPwr:
            print("  {:<15} {:0.4}  ".format("Tx Power(dBm)",
                  self._mw_to_dbm(channel.sensors.txPwr.value)), end="")
            print("  {:<15} {:0.4}  ".format("Tx Power(mW)",
                  channel.sensors.txPwr.value), end="")
        print("  {:<15} {:0.4}  ".format("Rx Power(dBm)",
              self._mw_to_dbm(channel.sensors.rxPwr.value)))
        print("  {:<15} {:0.4}  ".format("Rx Power(mW)",
              channel.sensors.rxPwr.value))

        if not self._verbose:
            return

        print("  {:<14}   {:>10} {:>15} {:>15} {:>10}".format(
            'Flags:', 'Alarm Low', 'Warning Low', 'Warning High', 'Alarm High'))

        print("    {:<14} {:>10} {:>15} {:>15} {:>10}".format(
            'Tx Bias(mA):',
            channel.sensors.txBias.flags.alarm.low,
            channel.sensors.txBias.flags.warn.low,
            channel.sensors.txBias.flags.warn.high,
            channel.sensors.txBias.flags.alarm.high))

        if channel.sensors.txPwr:
            print("    {:<14} {:>10} {:>15} {:>15} {:>10}".format(
                'Tx Power(dBm):',
                self._mw_to_dbm(channel.sensors.txPwr.flags.alarm.low),
                self._mw_to_dbm(channel.sensors.txPwr.flags.warn.low),
                self._mw_to_dbm(channel.sensors.txPwr.flags.warn.high),
                self._mw_to_dbm(channel.sensors.txPwr.flags.alarm.high)))
            print("    {:<14} {:>10} {:>15} {:>15} {:>10}".format(
                'Tx Power(mW):',
                channel.sensors.txPwr.flags.alarm.low,
                channel.sensors.txPwr.flags.warn.low,
                channel.sensors.txPwr.flags.warn.high,
                channel.sensors.txPwr.flags.alarm.high))
        print("    {:<14} {:>10} {:>15} {:>15} {:>10}".format(
            'Rx Power(dBm):',
            self._mw_to_dbm(channel.sensors.rxPwr.flags.alarm.low),
            self._mw_to_dbm(channel.sensors.rxPwr.flags.warn.low),
            self._mw_to_dbm(channel.sensors.rxPwr.flags.warn.high),
            self._mw_to_dbm(channel.sensors.rxPwr.flags.alarm.high)))
        print("    {:<14} {:>10} {:>15} {:>15} {:>10}".format(
            'Rx Power(mW):',
            channel.sensors.rxPwr.flags.alarm.low,
            channel.sensors.rxPwr.flags.warn.low,
            channel.sensors.rxPwr.flags.warn.high,
            channel.sensors.rxPwr.flags.alarm.high))

    def _print_transceiver_details(self, tid):  # noqa
        ''' Print details about transceiver '''

        info = self._info_resp[tid]
        ch_to_port = self._t_to_p[tid]
        if info.present is False:
            self._print_transceiver_ports(ch_to_port, info)
            return

        print("Transceiver:  {:>2}".format(info.port))
        if info.vendor:
            self._print_vendor_details(info)

        if info.cable:
            self._print_cable_details(info)

        if info.settings:
            self._print_settings_details(info)

        if info.sensor or (info.thresholds and self._verbose) or info.channels:
            print("Monitoring Information:")

        if info.sensor:
            print("  {:<15} {:0.4}   {:<4} {:0.4}".format("Temperature",
                  info.sensor.temp.value, "Vcc", info.sensor.vcc.value))

        if self._verbose and info.thresholds:
            self._print_thresholds(info.thresholds)

        if self._verbose and info.sensor:
            if info.sensor.temp.flags and info.sensor.vcc.flags:
                self._print_sensor_flags(info.sensor)

        for channel in info.channels:
            port = ch_to_port.get(channel.channel, None)
            if port:
                attrs = utils.get_status_strs(self._status_resp[port], None)
                print("  Channel: {}  Port: {:>2}  Status: {:<8}  Link: {:<4}"
                        .format(channel.channel, port, attrs['admin_status'],
                                attrs['link_status']))
            else:
                # This is a channel for a port we weren't asked to display.
                #
                # (It would probably be nicer to clean up the CLI syntax so it
                # is a bit less ambiguous about what we should do here when we
                # were only asked to display info for some ports in a given
                # transceiver.)
                print("  Channel: {}".format(channel.channel))

            self._print_port_channel(channel)

        # If we didn't print any channel info, print something useful
        if not info.channels:
            self._print_transceiver_ports(ch_to_port, info)

    def _print_port_detail(self):
        ''' print port details '''

        # If a port does not have a mapping to a transceiver, we should
        # still print it, lest we skip ports in the detail display.
        transceiver_printed = []
        for port, status in sorted(self._status_resp.items()):
            if status.transceiverIdx:
                tid = status.transceiverIdx.transceiverId
                if tid not in transceiver_printed:
                    self._print_transceiver_details(tid)
                transceiver_printed.append(tid)
            else:
                attrs = utils.get_status_strs(self._status_resp[port],
                            self._info_resp[status.transceiverIdx].present)
                print("Port: {:>2}  Status: {:<8}  Link: {:<4}  Transceiver: {}"
                      .format(port, attrs['admin_status'], attrs['link_status'],
                                attrs['present']))

    def get_detail_status(self):
        ''' Get port detail port status '''

        for port, status in sorted(self._status_resp.items()):
            if status.transceiverIdx:
                self._get_channel_detail(port, status)

        if not self._transceiver:
            return

        try:
            self._info_resp = \
                self._qsfp_client.getTransceiverInfo(self._transceiver)
        except TApplicationException:
            return

        self._get_dummy_status()
        self._print_port_detail()
