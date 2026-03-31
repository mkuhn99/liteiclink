#
# This file is part of LiteICLink.
#
# Copyright (c) 2017-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from litex.soc.interconnect        import stream
from litex.soc.interconnect.csr    import CSRStatus, CSRStorage
from litex.soc.interconnect.packet import HeaderField, Header
from litex.soc.interconnect.stream import CombinatorialActor, Endpoint, _rawbits_layout
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

# AXI

# Converter ----------------------------------------------------------------------------------------

class VT_UpConverter(LiteXModule):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.sink   = sink   = stream.Endpoint([("data", nbits_from)])
        self.source = source = stream.Endpoint([("data", nbits_to), ("valid_token_count", bits_for(ratio))])
        self.valid_token_count = Signal(bits_for(ratio))
        self.latency = 1

        # # #

        # Control path
        demux      = Signal(max=ratio)
        load_part  = Signal()
        strobe_all = Signal()
        self.comb += [
            sink.ready.eq(~strobe_all | source.ready),
            source.valid.eq(strobe_all),
            load_part.eq(sink.valid & sink.ready)
        ]

        demux_last = ((demux == (ratio - 1)) | sink.last)

        self.sync += [
            If(source.ready, strobe_all.eq(0)),
            If(load_part,
                If(demux_last,
                    demux.eq(0),
                    strobe_all.eq(1)
                ).Else(
                    demux.eq(demux + 1)
                )
            ),
            If(source.valid & source.ready,
                If(sink.valid & sink.ready,
                    source.first.eq(sink.first),
                    source.last.eq(sink.last)
                ).Else(
                    source.first.eq(0),
                    source.last.eq(0)
                )
            ).Elif(sink.valid & sink.ready,
                source.first.eq(sink.first | source.first),
                source.last.eq(sink.last | source.last)
            )
        ]

        # Data path
        cases = {}
        for i in range(ratio):
            n = ratio-i-1 if reverse else i
            cases[i] = source.data[n*nbits_from:(n+1)*nbits_from].eq(sink.data)
        self.sync += If(load_part, Case(demux, cases))

        # Valid token count
        self.sync += If(load_part, source.valid_token_count.eq(demux + 1), self.valid_token_count.eq(demux + 1))


class VT_DownConverter(LiteXModule):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.sink   = sink   = stream.Endpoint([("data", nbits_from)])
        self.source = source = stream.Endpoint([("data", nbits_to), ("valid_token_count", 1)])
        self.valid_token_count = Signal()
        self.latency = 0

        # # #

        # Control path
        mux   = Signal(max=ratio)
        first = Signal()
        last  = Signal()
        self.comb += [
            first.eq(mux == 0),
            last.eq(mux == (ratio-1)),
            source.valid.eq(sink.valid),
            source.first.eq(sink.first & first),
            source.last.eq(sink.last & last),
            sink.ready.eq(last & source.ready)
        ]
        self.sync += \
            If(source.valid & source.ready,
                If(last,
                    mux.eq(0)
                ).Else(
                    mux.eq(mux + 1)
                )
            )

        # Data path
        cases = {}
        for i in range(ratio):
            n = ratio-i-1 if reverse else i
            cases[i] = source.data.eq(sink.data[n*nbits_to:(n+1)*nbits_to])
        self.comb += Case(mux, cases).makedefault()

        # Valid token count
        self.comb += source.valid_token_count.eq(last)
        self.sync += self.valid_token_count.eq(last)


class VT_IdentityConverter(LiteXModule):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.sink   = sink   = stream.Endpoint([("data", nbits_from)])
        self.source = source = stream.Endpoint([("data", nbits_to), ("valid_token_count", 1)])
        self.valid_token_count = Signal()
        self.latency = 0

        # # #

        self.comb += [
            sink.connect(source),
            source.valid_token_count.eq(1),
        ]
        self.sync += self.valid_token_count.eq(1)


def _get_converter_ratio(nbits_from, nbits_to):
    if nbits_from > nbits_to:
        converter_cls = VT_DownConverter
        if nbits_from % nbits_to:
            raise ValueError("Ratio must be an int")
        ratio = nbits_from//nbits_to
    elif nbits_from < nbits_to:
        converter_cls = VT_UpConverter
        if nbits_to % nbits_from:
            raise ValueError("Ratio must be an int")
        ratio = nbits_to//nbits_from
    else:
        converter_cls = VT_IdentityConverter
        ratio = 1
    return converter_cls, ratio


class VT_Converter(Module): # FIXME: Switch to LiteXModule.
    def __init__(self, nbits_from, nbits_to,
        reverse                  = False,
        report_valid_token_count = False):
        self.cls, self.ratio = _get_converter_ratio(nbits_from, nbits_to)

        # # #

        converter = self.cls(nbits_from, nbits_to, self.ratio, reverse)
        self.submodules += converter
        self.valid_token_count = converter.valid_token_count
        self.latency = converter.latency

        self.sink = converter.sink
        if report_valid_token_count:
            self.source = converter.source
        else:
            self.source = stream.Endpoint([("data", nbits_to)])
            self.comb += converter.source.connect(self.source, omit=set(["valid_token_count"]))
# Cast ---------------------------------------------------------------------------------------------

class FullCast(CombinatorialActor):
    def __init__(self, layout_from, layout_to, reverse_from=False, reverse_to=False):
        self.sink   = Endpoint(_rawbits_layout(layout_from))
        self.source = Endpoint(_rawbits_layout(layout_to))
        CombinatorialActor.__init__(self)

        # # #

        sigs_from = self.sink.payload.flatten() + self.sink.param.flatten()
        if reverse_from:
            sigs_from = list(reversed(sigs_from))
        sigs_to = self.source.payload.flatten() + self.source.param.flatten()
        if reverse_to:
            sigs_to = list(reversed(sigs_to))
        if sum(len(s) for s in sigs_from) != sum(len(s) for s in sigs_to):
            raise TypeError
        self.comb += Cat(*sigs_to).eq(Cat(*sigs_from))

# Packetizer ---------------------------------------------------------------------------------------
class AxiPacketizer(LiteXModule):
    def __init__(self, axi_endpoint, packet_size=8):
        dw = sum([c[1] for c in axi_endpoint.description.payload_layout + axi_endpoint.description.param_layout ]) + 2
        padded_dw = -(-dw//packet_size)*packet_size
        pad_w = padded_dw - dw
        if pad_w != 0:
            self.padded_endpoint = padded_endpoint = stream.Endpoint(stream.EndpointDescription(axi_endpoint.description.payload_layout + [('pad', pad_w), ('last_', 1), ('first_', 1)], axi_endpoint.description.param_layout))
        else:
            self.padded_endpoint = padded_endpoint = stream.Endpoint(stream.EndpointDescription(axi_endpoint.description.payload_layout + [('last_', 1), ('first_', 1)], axi_endpoint.description.param_layout))
        self.source = source = stream.Endpoint(phy_description(packet_size))
        self.sink   = sink   = stream.Endpoint([('data', padded_dw)])
        self.cast = cast = FullCast(self.padded_endpoint.description, sink.description)
        self.converter = converter = VT_Converter(padded_dw, packet_size, report_valid_token_count=True)
        self.comb += [
            axi_endpoint.connect(padded_endpoint, omit={'pad', 'last_', 'first_'}),
            padded_endpoint.last_.eq(axi_endpoint.last),
            padded_endpoint.first_.eq(axi_endpoint.first),
            padded_endpoint.connect(cast.sink),
            cast.source.connect(sink),
            sink.connect(converter.sink),
            ]
        self.transaction_cycles = CSRStorage(32, reset=0, write_from_dev=True)
        self.transaction_counter = CSRStorage(32, reset=0, write_from_dev=True)
        self.source_notready_counter = CSRStorage(32, reset=0, write_from_dev=True)

        magic_word = 0x5aa55aa5 if packet_size == 32 else 0x5a
        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(0),
            If(sink.valid,
                If(source.ready,
                    NextState("DATA"),
                    source.valid.eq(1),
                    source.data.eq(magic_word),
                )
            )
        )
        fsm.act("DATA",
            NextValue(self.transaction_cycles.storage, self.transaction_cycles.storage + 1),
            If(source.ready,
                source.valid.eq(sink.valid),
                source.data.eq(converter.source.data),
                sink.ready.eq(converter.source.valid_token_count),
                converter.source.ready.eq(1),
            ).Else(
                NextValue(self.source_notready_counter.storage, self.source_notready_counter.storage + 1),
            ),
            If(converter.sink.ready,
                NextState("PREAMBLE"),
                NextValue(self.transaction_counter.storage, self.transaction_counter.storage + 1)
            )
        )

# Depacketizer -------------------------------------------------------------------------------------

class AxiDepacketizer(LiteXModule):
    def __init__(self, clk_freq, axi_endpoint, timeout=10, buffer_depth=16, packet_size=8):
        dw = sum([c[1] for c in axi_endpoint.description.payload_layout + axi_endpoint.description.param_layout]) + 2
        padded_dw = -(-dw//packet_size)*packet_size
        pad_w = padded_dw - dw
        if pad_w != 0:
            self.padded_endpoint = stream.Endpoint(stream.EndpointDescription(axi_endpoint.description.payload_layout + [('pad', pad_w)] + [('last_', 1), ('first_', 1)], axi_endpoint.description.param_layout))
        else:
            self.padded_endpoint = stream.Endpoint(stream.EndpointDescription(axi_endpoint.description.payload_layout + [('last_', 1), ('first_', 1)], axi_endpoint.description.param_layout))
        self.source   = source   = stream.Endpoint(phy_description(padded_dw))
        self.cast = FullCast(source.description, self.padded_endpoint.description)
        self.sink = sink = stream.Endpoint(phy_description(packet_size))
        self.converter = converter = VT_Converter(packet_size, padded_dw, report_valid_token_count=True)
        self.buffer = stream.SyncFIFO(self.padded_endpoint.description, buffer_depth)
        self.packet_counter = Signal(5)
        self.num_packets = -(-dw//packet_size)
        self.comb += [
            self.converter.source.connect(self.source, omit={'valid_token_count'}),
            self.source.connect(self.cast.sink),
            self.cast.source.connect(self.buffer.sink),
            self.buffer.source.connect(self.padded_endpoint),
            self.padded_endpoint.connect(axi_endpoint, omit={'pad', 'last_', 'first_'}),
            axi_endpoint.last.eq(self.padded_endpoint.last_),
            axi_endpoint.first.eq(self.padded_endpoint.first_),
        ]
        self.transaction_cycles = CSRStorage(32, reset=0, write_from_dev=True)
        self.transaction_counter = CSRStorage(32, reset=0, write_from_dev=True)
        self.timeout_counter = CSRStorage(32, reset=0, write_from_dev=True)
        self.sink_invalid_counter = CSRStorage(32, reset=0, write_from_dev=True)
        # # #

        # Timer.
        # ------
        self.timer = timer = WaitTimer(clk_freq*timeout)
        magic_word = 0x5aa55aa5 if packet_size == 32 else 0x5a
        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(1),
            converter.sink.valid.eq(0),
            If(sink.valid &
              (sink.data == magic_word),
                NextState("DATA"),
                NextValue(converter.valid_token_count, 0),
            )
        )
        fsm.act("DATA",
            sink.ready.eq(1),
            NextValue(self.transaction_cycles.storage, self.transaction_cycles.storage + 1),
            NextValue(self.packet_counter, self.packet_counter + 1),
            If(sink.valid,
                converter.sink.valid.eq(1),
                converter.sink.data.eq(sink.data),
            ).Else(
                NextValue(self.sink_invalid_counter.storage, self.sink_invalid_counter.storage + 1),
            ),
            If(timer.done,
                NextState("PREAMBLE"),
                NextValue(self.timeout_counter.storage, self.timeout_counter.storage + 1),
            ),
            If( self.packet_counter >= (self.num_packets - 1),
                    NextState("PREAMBLE"),
                    NextValue(self.packet_counter, 0),
                    NextValue(self.transaction_counter.storage, self.transaction_counter.storage + 1),
                ),
            timer.wait.eq(1)
        )
