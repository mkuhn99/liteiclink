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

def packet_description_(payload_layout):
    param_layout = [("port", 8), ("length", 16)]
    return stream.EndpointDescription(payload_layout, param_layout)

def phy_description(dw):
    layout = [("data", dw)]
    return stream.EndpointDescription(layout)

# Packetizer ---------------------------------------------------------------------------------------
# TODO: rewrite for other packet_descriptions
class Packetizer(LiteXModule):
    def __init__(self, packet_descr=packet_description(32)):
        self.sink   = sink   = stream.Endpoint(packet_descr)
        dw = sum([c[1] for c in sink.description.payload_layout])
        self.source = source = stream.Endpoint(phy_description(dw))

        # # #

        # Packet description
        # - Preamble : 4 bytes.
        # - Port     : 1 byte.
        # - Length   : 2 bytes.
        # - Payload  : length bytes.

        # FSM.
        # ----
        i = 0
        data_write = ()
        for c in sink.description.payload_layout:
            c_sig = getattr(sink, c[0])
            c_width = c[1]
            data_write += (source.data[i:i+c_width].eq(c_sig),)
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
            # source.data.eq(sink.data),
            data_write,
            sink.ready.eq(source.ready),
            If(source.ready & sink.last,
                NextState("PREAMBLE")
            )
        )

# Depacketizer -------------------------------------------------------------------------------------

class Depacketizer(LiteXModule):
    def __init__(self, clk_freq, timeout=10, packet_descr=packet_description(32)):
        self.source   = source   = stream.Endpoint(packet_descr)
        dw = sum([c[1] for c in source.description.payload_layout])
        self.sink = sink = stream.Endpoint(phy_description(dw))

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
        i = 0
        data_write = ()
        for c in source.description.payload_layout:
            c_sig = getattr(source, c[0])
            c_width = c[1]
            data_write += (sink.data[i:i+c_width].eq(c_sig),)

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
            data_write,
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
