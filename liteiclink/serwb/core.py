#
# This file is part of LiteICLink.
#
# Copyright (c) 2017-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex.soc.interconnect        import stream
from litex.soc.interconnect.axi    import AXILiteInterface, ax_lite_description, w_lite_description, b_lite_description, r_lite_description
from litex.soc.interconnect.packet import Arbiter, Dispatcher

from liteiclink.serwb.packet    import packet_description, packet_description_
from liteiclink.serwb.packet    import Packetizer, Depacketizer
from liteiclink.serwb.etherbone import Etherbone

# SERWB Core ---------------------------------------------------------------------------------------

class SERWBCore(LiteXModule):
    def __init__(self, phy, clk_freq, mode, with_rst_on_link_down=True, port=0,
        etherbone_buffer_depth = 4,
        tx_buffer_depth        = 8,
        rx_buffer_depth        = 8,
    ):
        # Downstream/Upstream Endpoints.
        # ------------------------------
        self.downstream_endpoints = {}
        self.upstream_endpoints   = {}

        # Etherbone.
        # ----------
        self.etherbone = etherbone = ResetInserter()(Etherbone(mode, etherbone_buffer_depth))
        self.add_downstream_endpoint(port=port, endpoint=etherbone.source)
        self.add_upstream_endpoint(  port=port, endpoint=etherbone.sink)

        # Bus.
        # ----
        self.bus = etherbone.wishbone.bus

        # Packetizer / Depacketizer.
        # --------------------------
        self.packetizer   = packetizer   = ResetInserter()(Packetizer())
        self.depacketizer = depacketizer = ResetInserter()(Depacketizer(clk_freq))

        # Buffering.
        # ----------
        self.tx_fifo = tx_fifo = ResetInserter()(stream.SyncFIFO([("data", 32)], tx_buffer_depth, buffered=True))
        self.rx_fifo = rx_fifo = ResetInserter()(stream.SyncFIFO([("data", 32)], rx_buffer_depth, buffered=True))

        # Data-Path.
        # ----------
        self.comb += [
            # Core -> PHY.
            packetizer.source.connect(tx_fifo.sink),
            tx_fifo.source.connect(phy.sink),

            # PHY -> Core.
            phy.source.connect(rx_fifo.sink),
            rx_fifo.source.connect(depacketizer.sink),
        ]

        # Reset internal module when link down.
        # -------------------------------------
        if with_rst_on_link_down:
            self.comb += [
                etherbone.reset.eq(    ~phy.init.ready),
                packetizer.reset.eq(   ~phy.init.ready),
                depacketizer.reset.eq( ~phy.init.ready),
                tx_fifo.reset.eq(      ~phy.init.ready),
                rx_fifo.reset.eq(      ~phy.init.ready),
            ]

    def add_downstream_endpoint(self, port, endpoint):
        if port in self.downstream_endpoints.keys():
            raise ValueError(f"Downstream endpoint for port {port} already exists.")
        self.downstream_endpoints[port] = endpoint


    def add_upstream_endpoint(self, port, endpoint):
        if port in self.upstream_endpoints.keys():
            raise ValueError(f"Upstream endpoint for port {port} already exists.")
        self.upstream_endpoints[port] = endpoint

    def do_finalize(self):
        # Downstream Arbitration.
        # -----------------------
        downstream_endpoints = [stream.Endpoint(packet_description(32)) for _ in range(len(self.downstream_endpoints))]
        for i, (k, v) in enumerate(self.downstream_endpoints.items()):
            self.comb += [
                v.connect(downstream_endpoints[i], keep={"valid", "ready", "last", "data", "length"}),
                downstream_endpoints[i].port.eq(k),
            ]
        self.arbiter = Arbiter(
            masters = downstream_endpoints,
            slave   = self.packetizer.sink,
        )

        # Upstream Dispatching.
        # ---------------------
        self.dispatcher = Dispatcher(
            master  = self.depacketizer.source,
            slaves  = [ep for _, ep in self.upstream_endpoints.items()],
            one_hot = False,
            keep    = {"valid", "ready", "last", "data", "length"},
        )
        for i, (k, v) in enumerate(self.upstream_endpoints.items()):
            self.comb += If(self.depacketizer.source.port == k, self.dispatcher.sel.eq(i))

# SERWB Core ---------------------------------------------------------------------------------------

class SERWBCoreAXILite(LiteXModule):
    def __init__(self, phys, clk_freq, mode, with_rst_on_link_down=True,
        buffer_depth        = 8,
    ):
        assert mode in ['master', 'slave'], "mode has to be master or slave"
        # Bus.
        # ----
        # TODO: Master/Slave distinction            
        self.bus = AXILiteInterface()

        # Buffering.
        # ----------
        self.aw_fifo           = aw_fifo    = ResetInserter()(stream.SyncFIFO([('data', sum([c[1] for c in ax_lite_description(32)]))], buffer_depth, buffered=True))
        self.w_fifo            = w_fifo     = ResetInserter()(stream.SyncFIFO([('data', sum([c[1] for c in w_lite_description(32)]))], buffer_depth, buffered=True))
        self.ar_fifo           = ar_fifo    = ResetInserter()(stream.SyncFIFO([('data', sum([c[1] for c in ax_lite_description(32)]))], buffer_depth, buffered=True))
        self.b_fifo            = b_fifo     = ResetInserter()(stream.SyncFIFO([('data', sum([c[1] for c in b_lite_description()]))], buffer_depth, buffered=True))
        self.r_fifo            = r_fifo     = ResetInserter()(stream.SyncFIFO([('data', sum([c[1] for c in r_lite_description(32)]))], buffer_depth, buffered=True))

        # Packetizer / Depacketizer.
        # --------------------------

        #TODO: make smart loop
        if mode=="master":
            self.aw_packetizer      = aw_packetizer     = ResetInserter()(Packetizer(packet_descr=packet_description_(ax_lite_description(32))))
            self.w_packetizer       = w_packetizer      = ResetInserter()(Packetizer(packet_descr=packet_description_(w_lite_description(32))))
            self.ar_packetizer      = ar_packetizer     = ResetInserter()(Packetizer(packet_descr=packet_description_(ax_lite_description(32))))
            self.b_depacketizer     = b_depacketizer    = ResetInserter()(Depacketizer(clk_freq, packet_descr=packet_description_(b_lite_description())))
            self.r_depacketizer     = r_depacketizer    = ResetInserter()(Depacketizer(clk_freq, packet_descr=packet_description_(r_lite_description(32))))
            self.comb += [
                # AXIInterfaceLite <---> Core.
                self.bus.aw.connect(aw_packetizer.sink),
                self.bus.ar.connect(ar_packetizer.sink),
                self.bus.w.connect(w_packetizer.sink),
                b_depacketizer.source.connect(self.bus.b, omit={'length', 'port'}),
                r_depacketizer.source.connect(self.bus.r, omit={'length', 'port'}),
                # Core -> PHY.
                aw_packetizer.source.connect(aw_fifo.sink),
                w_packetizer.source.connect(w_fifo.sink),
                ar_packetizer.source.connect(ar_fifo.sink),
                # packetizer.source.connect(tx_fifo.sink),
                # tx_fifo.source.connect(phy.sink),
                aw_fifo.source.connect(phys['aw'].sink),
                w_fifo.source.connect(phys['w'].sink),
                ar_fifo.source.connect(phys['ar'].sink),

                # PHY -> Core.
                # phy.source.connect(rx_fifo.sink),
                # rx_fifo.source.connect(depacketizer.sink),
                b_fifo.source.connect(b_depacketizer.sink, omit={'length', 'port'}),
                r_fifo.source.connect(r_depacketizer.sink, omit={'length', 'port'}),
                phys['b'].source.connect(b_fifo.sink),
                phys['r'].source.connect(r_fifo.sink),
            ]

        else:
            self.aw_depacketizer    = aw_depacketizer       = ResetInserter()(Depacketizer(clk_freq, packet_descr=packet_description_(ax_lite_description(32))))
            self.w_depacketizer     = w_depacketizer        = ResetInserter()(Depacketizer(clk_freq, packet_descr=packet_description_(w_lite_description(32))))
            self.ar_depacketizer    = ar_depacketizer       = ResetInserter()(Depacketizer(clk_freq, packet_descr=packet_description_(ax_lite_description(32))))
            self.b_packetizer       = b_packetizer          = ResetInserter()(Packetizer(packet_descr=packet_description_(b_lite_description())))
            self.r_packetizer       = r_packetizer          = ResetInserter()(Packetizer(packet_descr=packet_description_(r_lite_description(32))))
            self.comb += [
                # AXIInterfaceLite <---> Core
                self.bus.b.connect(b_packetizer.sink),
                self.bus.r.connect(r_packetizer.sink),
                w_depacketizer.source.connect(self.bus.w, omit={'length', 'port'}),
                aw_depacketizer.source.connect(self.bus.aw, omit={'length', 'port'}),
                ar_depacketizer.source.connect(self.bus.ar, omit={'length', 'port'}),
                # Core -> PHY.
                b_packetizer.source.connect(b_fifo.sink),
                r_packetizer.source.connect(r_fifo.sink),
                # packetizer.source.connect(tx_fifo.sink),
                # tx_fifo.source.connect(phy.sink),
                b_fifo.source.connect(phys['b'].sink),
                r_fifo.source.connect(phys['r'].sink),

                # PHY -> Core.
                # phy.source.connect(rx_fifo.sink),
                # rx_fifo.source.connect(depacketizer.sink),
                aw_fifo.source.connect(aw_depacketizer.sink, omit={'length', 'port'}),
                ar_fifo.source.connect(ar_depacketizer.sink, omit={'length', 'port'}),
                w_fifo.source.connect(w_depacketizer.sink, omit={'length', 'port'}),
                phys['aw'].source.connect(aw_fifo.sink),
                phys['w'].source.connect(w_fifo.sink),
                phys['ar'].source.connect(ar_fifo.sink),
            ]

        # Reset internal module when link down.
        # -------------------------------------
        # TODO: reset
        # if with_rst_on_link_down:
        #     self.comb += [
        #         packetizer.reset.eq(   ~phy.init.ready),
        #         depacketizer.reset.eq( ~phy.init.ready),
        #         tx_fifo.reset.eq(      ~phy.init.ready),
        #         rx_fifo.reset.eq(      ~phy.init.ready),
        #     ]

    # def do_finalize(self):
        # Downstream Arbitration.
        # -----------------------
        # downstream_endpoints = [stream.Endpoint(packet_description(32)) for _ in range(len(self.downstream_endpoints))]
        # for i, (k, v) in enumerate(self.downstream_endpoints.items()):
        #     self.comb += [
        #         v.connect(downstream_endpoints[i], keep={"valid", "ready", "last", "data", "length"}),
        #         downstream_endpoints[i].port.eq(k),
        #     ]
        # self.arbiter = Arbiter(
        #     masters = downstream_endpoints,
        #     slave   = self.packetizer.sink,
        # )

        # # Upstream Dispatching.
        # # ---------------------
        # self.dispatcher = Dispatcher(
        #     master  = self.depacketizer.source,
        #     slaves  = [ep for _, ep in self.upstream_endpoints.items()],
        #     one_hot = False,
        #     keep    = {"valid", "ready", "last", "data", "length"},
        # )
        # for i, (k, v) in enumerate(self.upstream_endpoints.items()):
        #     self.comb += If(self.depacketizer.source.port == k, self.dispatcher.sel.eq(i))


# SERIO Core ---------------------------------------------------------------------------------------

class SERIOPacketizer(LiteXModule):
    def __init__(self):
        self.i      = Signal(32)
        self.source = source = stream.Endpoint(packet_description(32))

        # # #

        # Signals.
        # --------
        i   = Signal(32)
        i_d = Signal(32)

        # Re-Synchronize Inputs.
        # ----------------------
        self.specials += MultiReg(self.i, i)

        # Register Inputs.
        # ----------------
        self.sync += If(source.ready, i_d.eq(i))

        # Generate Packet.
        # ----------------
        self.comb += [
            source.valid.eq(i != i_d),
            source.last.eq(1),
            source.data.eq(i),
            source.length.eq(4),
        ]

class SERIODepacketizer(LiteXModule):
    def __init__(self):
        self.sink = sink = stream.Endpoint(packet_description(32))
        self.o    = Signal(32)

        # # #

        # Generate Outputs.
        # -----------------
        self.comb += sink.ready.eq(1)
        self.sync += If(sink.valid & sink.last, self.o.eq(sink.data))

class SERIOCore(LiteXModule):
    def __init__(self, serwb_core, port=1):
        self.i = Signal(32)
        self.o = Signal(32)

        # # #

        # Packetizer.
        # -----------
        self.packetizer = SERIOPacketizer()
        self.comb += self.packetizer.i.eq(self.i)

        # Depacketizer.
        # -------------
        self.depacketizer = SERIODepacketizer()
        self.comb += self.o.eq(self.depacketizer.o)

        # Add to SERWB Downstreams/Upstreams Endpoints.
        # ---------------------------------------------
        serwb_core.add_downstream_endpoint(port=port, endpoint=self.packetizer.source)
        serwb_core.add_upstream_endpoint  (port=port, endpoint=self.depacketizer.sink)
