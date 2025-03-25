#
# This file is part of LiteICLink.
#
# Copyright (c) 2017-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from litex.soc.interconnect        import stream
from litex.soc.interconnect.packet import HeaderField, Header

# Layouts ------------------------------------------------------------------------------------------

def packet_description(dw):
    payload_layout = [("data", dw)]
    param_layout   = [("port", 8), ("length", 16)]
    return stream.EndpointDescription(payload_layout, param_layout)

def phy_description(dw):
    layout = [("data", dw)]
    return stream.EndpointDescription(layout)

def packet_description_(payload_layout):
    param_layout = [("port", 8), ("length", 16)]
    return stream.EndpointDescription(payload_layout, param_layout)

# Packetizer ---------------------------------------------------------------------------------------

class Packetizer(LiteXModule):
    def __init__(self):
        self.sink   = sink   = stream.Endpoint(packet_description(32))
        self.source = source = stream.Endpoint(phy_description(32))

        # # #

        # Packet description
        # - Preamble : 4 bytes.
        # - Port     : 1 byte.
        # - Length   : 2 bytes.
        # - Payload  : length bytes.

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            If(sink.valid,
                source.valid.eq(1),
                source.data.eq(0x5aa55aa5),
                If(source.ready,
                    NextState("PORT-LENGTH")
                )
            )
        )
        fsm.act("PORT-LENGTH",
            source.valid.eq(1),
            source.data[0 :8].eq(sink.port),
            source.data[8:24].eq(sink.length),
            If(source.ready,
                NextState("DATA")
            )
        )
        fsm.act("DATA",
            source.valid.eq(sink.valid),
            source.data.eq(sink.data),
            sink.ready.eq(source.ready),
            If(source.ready & sink.last,
                NextState("PREAMBLE")
            )
        )

# Depacketizer -------------------------------------------------------------------------------------

class Depacketizer(LiteXModule):
    def __init__(self, clk_freq, timeout=10):
        self.sink   = sink   = stream.Endpoint(phy_description(32))
        self.source = source = stream.Endpoint(packet_description(32))

        # # #

        # Packet description
        # - Preamble : 4 bytes.
        # - Port     : 1 byte.
        # - Length   : 2 bytes.
        # - Payload

        # Signals.
        # --------
        port   = Signal(len(source.port))
        count  = Signal(len(source.length))
        length = Signal(len(source.length))

        # Timer.
        # ------
        self.timer = timer = WaitTimer(clk_freq*timeout)

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(1),
            If(sink.valid &
              (sink.data == 0x5aa55aa5),
                NextState("PORT-LENGTH")
            )
        )
        fsm.act("PORT-LENGTH",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(count, 0),
                NextValue(port,   sink.data[0:8]),
                NextValue(length, sink.data[8:24]),
                NextState("DATA")
            ),
            timer.wait.eq(1)
        )
        fsm.act("DATA",
            source.valid.eq(sink.valid),
            source.last.eq(count == (length[2:] - 1)),
            source.port.eq(port),
            source.length.eq(length),
            source.data.eq(sink.data),
            sink.ready.eq(source.ready),
            If(timer.done,
                NextState("PREAMBLE")
            ).Elif(source.valid & source.ready,
                NextValue(count, count + 1),
                If(source.last,
                    NextState("PREAMBLE")
                )
            ),
            timer.wait.eq(1)
        )

# Packetizer ---------------------------------------------------------------------------------------
# TODO: 
# write payload in 32bit packets in loop
#   -> flatten/cast payload
#   -> iterate over payload with converter ?
# e.g.: for p32bit in p: 
#           ... fsm.act(...)
class AxiPacketizer(LiteXModule):
    def __init__(self, axi_endpoint):
        dw = sum([c[1] for c in axi_endpoint.description.payload_layout])
        padded_dw = -(-dw//32)*32
        pad_w = padded_dw - dw
        length = padded_dw//32
        if pad_w != 0:
            self.padded_endpoint = padded_endpoint = stream.Endpoint(axi_endpoint.description.payload_layout + [('pad', pad_w)])
        else:
            self.padded_endpoint = padded_endpoint = stream.Endpoint(axi_endpoint.description.payload_layout)
        self.source = source = stream.Endpoint(phy_description(32))
        self.sink   = sink   = stream.Endpoint([('data', padded_dw)])
        self.cast = cast = stream.Cast(self.padded_endpoint.description, sink.description)
        self.converter = converter = stream.Converter(padded_dw, 32, report_valid_token_count=True)
        self.comb += [
            axi_endpoint.connect(padded_endpoint, omit={'pad'}),
            padded_endpoint.connect(cast.sink),
            cast.source.connect(sink),
            sink.connect(converter.sink),
            ]

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(0),
            If(sink.valid,
                source.valid.eq(1),
                source.data.eq(0x5aa55aa5),
                If(source.ready,
                    NextState("DATA")
                )
            )
        )
        fsm.act("DATA",
            source.valid.eq(sink.valid),
            source.data.eq(converter.source.data),
            sink.ready.eq(converter.source.valid_token_count),
            converter.source.ready.eq(1),
            If(converter.source.valid_token_count,
                NextState("PREAMBLE")
            )
        )

# Depacketizer -------------------------------------------------------------------------------------

class AxiDepacketizer(LiteXModule):
    def __init__(self, clk_freq, axi_endpoint, timeout=10):
        dw = sum([c[1] for c in axi_endpoint.description.payload_layout])
        padded_dw = -(-dw//32)*32
        pad_w = padded_dw - dw
        if pad_w != 0:
            self.padded_endpoint = stream.Endpoint(axi_endpoint.description.payload_layout + [('pad', pad_w)])
        else:
            self.padded_endpoint = stream.Endpoint(axi_endpoint.description.payload_layout)
        length = padded_dw//32
        self.source   = source   = stream.Endpoint(phy_description(padded_dw))
        self.cast = stream.Cast(source.description, self.padded_endpoint.description)
        self.sink = sink = stream.Endpoint(phy_description(32))
        self.converter = converter = stream.Converter(32, padded_dw, report_valid_token_count=True)
        self.comb += [
            self.converter.source.connect(self.source, omit={'valid_token_count'}),
            self.source.connect(self.cast.sink),
            self.cast.source.connect(self.padded_endpoint),
            self.padded_endpoint.connect(axi_endpoint, omit={'pad'}),
        ]

        # # #

        # Timer.
        # ------
        self.timer = timer = WaitTimer(clk_freq*timeout)
        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(1),
            If(sink.valid &
              (sink.data == 0x5aa55aa5),
                NextState("DATA"),
                NextValue(converter.valid_token_count, 0),
            ),
        )
        fsm.act("DATA",
            sink.ready.eq(1),
            If(sink.valid,
                converter.sink.valid.eq(1),
                converter.sink.data.eq(sink.data),
                source.ready.eq(converter.valid_token_count == length),
                If(length == 1,
                    NextState("PREAMBLE"),
                )
            ),
            If(timer.done,
                NextState("PREAMBLE"),
            ),
            If((converter.valid_token_count == length) & (converter.valid_token_count != 1),
                    NextState("PREAMBLE"),
                ),
            timer.wait.eq(1)
        )
