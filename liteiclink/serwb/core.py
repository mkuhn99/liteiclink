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

from liteiclink.serwb.packet    import packet_description, packet_description_, phy_description
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

# PHYs:
#                  Master   <------------>   Slave
# aw: 
#                            aw_addr 32 ->
# w:
#                             w_data 32 ->
# ctrl: 
#                             ar_prot 3 ->
#                             aw_prot 3 ->
#                              w_strb 4 ->
#                           <- 2 b_resp
#                           <- 2 r_resp
# r:  
#                            ar_addr 32 ->
#                           <- 32 r_data

class SERWBCoreAXILite(LiteXModule):
    def __init__(self, phys, clk_freq, mode, with_rst_on_link_down=True,
        buffer_depth        = 8,
    ):
        assert mode in ['master', 'slave'], "mode has to be master or slave"
        assert len(phys) == 4, "need for phys"
        # Bus.
        # ----
        # TODO: Master/Slave distinction            
        self.bus = AXILiteInterface()
        self.aw_addr_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.ar_addr_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.w_data_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.r_data_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.ctrl_endpoint0 = stream.Endpoint(stream.EndpointDescription([('ar_prot', 3), ('ar_valid', 1), ('aw_prot', 3), ('aw_valid', 1), ('w_strb', 4), ('w_valid', 1), ('pad', 19)]))
        self.ctrl_endpoint1 = stream.Endpoint(stream.EndpointDescription([('b_resp', 3), ('b_valid', 1), ('r_resp', 3), ('r_valid', 1), ('pad', 24)]))
        
        # Buffering.
        # ----------
        self.aw_fifo     = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))
        self.ar_fifo     = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))
        self.w_fifo      = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))
        self.ctrl0_fifo  = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))
        self.ctrl1_fifo  = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))
        self.r_fifo      = ResetInserter()(stream.SyncFIFO([('data', 32)], buffer_depth, buffered=True))

        # Packetizer / Depacketizer.
        # --------------------------

        #TODO: make smart loop
        if mode=="slave":
            self.ctrl0_cast = stream.Cast(self.ctrl_endpoint0.description, [('data', 32)])
            self.ctrl1_cast = stream.Cast([('data', 32)], self.ctrl_endpoint1.description)
            self.comb += [
                # Master -> Slave Signals
                self.aw_addr_endpoint.ready.eq(phys['aw'].sink.ready),
                self.w_data_endpoint.ready.eq(phys['w'].sink.ready),
                self.ar_addr_endpoint.ready.eq(phys['r'].sink.ready),
                self.r_data_endpoint.ready.eq(phys['r'].source.ready),
                self.ctrl_endpoint0.ready.eq(phys['ctrl'].sink.ready),
                self.ctrl_endpoint1.ready.eq(phys['ctrl'].source.ready),

                self.bus.aw.ready.eq(self.aw_addr_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.ar.ready.eq(self.ar_addr_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.w.ready.eq(self.w_data_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.b.ready.eq(self.ctrl_endpoint1.ready),
                self.bus.r.ready.eq(self.r_data_endpoint.ready & self.ctrl_endpoint0.ready),
                # Bus-Channels -> 32bit-Channels
                self.aw_addr_endpoint.data.eq(self.bus.aw.addr),
                self.aw_addr_endpoint.valid.eq(self.bus.aw.valid),

                self.ar_addr_endpoint.data.eq(self.bus.ar.addr),
                self.ar_addr_endpoint.valid.eq(self.bus.ar.valid),

                self.w_data_endpoint.data.eq(self.bus.w.data),
                self.w_data_endpoint.valid.eq(self.bus.w.valid),

                self.ctrl_endpoint0.valid.eq(self.bus.ar.valid | self.bus.aw.valid | self.bus.w.valid),
                self.ctrl_endpoint0.ar_prot.eq(self.bus.ar.prot),
                self.ctrl_endpoint0.aw_prot.eq(self.bus.aw.prot),
                self.ctrl_endpoint0.w_strb.eq(self.bus.w.strb),
                self.ctrl_endpoint0.ar_valid.eq(self.bus.ar.valid),
                self.ctrl_endpoint0.aw_valid.eq(self.bus.aw.valid),
                self.ctrl_endpoint0.w_valid.eq(self.bus.w.valid),

                # 32bit-Channels -> FIFO
                self.aw_addr_endpoint.connect(self.aw_fifo.sink),
                self.ar_addr_endpoint.connect(self.ar_fifo.sink),
                self.ctrl_endpoint0.connect(self.ctrl0_cast.sink),
                self.ctrl0_cast.source.connect(self.ctrl0_fifo.sink),
                self.w_data_endpoint.connect(self.w_fifo.sink),

                # FIFO -> PHY
                self.aw_fifo.source.connect(phys['aw'].sink),
                self.ar_fifo.source.connect(phys['r'].sink),
                self.ctrl0_fifo.source.connect(phys['ctrl'].sink),
                self.w_fifo.source.connect(phys['w'].sink),

                # Master <- Slave
                # PHY -> FIFO
                phys['r'].source.connect(self.r_fifo.sink),
                phys['ctrl'].source.connect(self.ctrl1_fifo.sink),

                # FIFO -> 32bit-Channels
                self.r_fifo.source.connect(self.r_data_endpoint),
                self.ctrl1_fifo.source.connect(self.ctrl1_cast.sink),
                self.ctrl1_cast.source.connect(self.ctrl_endpoint1),

                # 32bit-Channels -> FIFO
                self.bus.r.data.eq(self.r_data_endpoint.data),
                self.bus.r.resp.eq(self.ctrl_endpoint1.r_resp),
                self.bus.r.valid.eq(self.r_data_endpoint.valid & self.ctrl_endpoint1.valid & self.ctrl_endpoint1.r_valid),

                self.bus.b.resp.eq(self.ctrl_endpoint1.b_resp),
                self.bus.b.valid.eq(self.ctrl_endpoint1.b_valid & self.ctrl_endpoint1.valid),
            ]

        else:
            self.ctrl1_cast = stream.Cast(self.ctrl_endpoint1.description, [('data', 32)])
            self.ctrl0_cast = stream.Cast([('data', 32)], self.ctrl_endpoint0.description)
            self.comb += [
                # Slave -> Master Signals TODO: sink.ready -> phy init ready?
                self.aw_addr_endpoint.ready.eq(phys['aw'].source.ready),
                self.w_data_endpoint.ready.eq(phys['w'].source.ready),
                self.ar_addr_endpoint.ready.eq(phys['r'].source.ready),
                self.r_data_endpoint.ready.eq(phys['r'].sink.ready),
                self.ctrl_endpoint0.ready.eq(phys['ctrl'].source.ready),
                self.ctrl_endpoint1.ready.eq(phys['ctrl'].sink.ready),

                self.bus.aw.ready.eq(self.aw_addr_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.ar.ready.eq(self.ar_addr_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.w.ready.eq(self.w_data_endpoint.ready & self.ctrl_endpoint0.ready),
                self.bus.b.ready.eq(self.ctrl_endpoint1.ready),
                self.bus.r.ready.eq(self.r_data_endpoint.ready & self.ctrl_endpoint0.ready),
                # bus -> 32bit-Channels
                self.r_data_endpoint.data.eq(self.bus.r.data),
                self.r_data_endpoint.valid.eq(self.bus.r.valid),

                self.ctrl_endpoint1.valid.eq(self.bus.b.valid | self.bus.r.valid),
                self.ctrl_endpoint1.b_resp.eq(self.bus.b.resp),
                self.ctrl_endpoint1.b_valid.eq(self.bus.b.valid),
                self.ctrl_endpoint1.r_resp.eq(self.bus.r.resp),
                self.ctrl_endpoint1.r_valid.eq(self.bus.r.valid),

                # 32bit-Channels -> FIFO
                self.r_data_endpoint.connect(self.r_fifo.sink),
                self.ctrl_endpoint1.connect(self.ctrl1_cast.sink),
                self.ctrl1_cast.source.connect(self.ctrl1_fifo.sink),

                # FIFO -> PHY
                self.r_fifo.source.connect(phys['r'].sink),
                self.ctrl1_fifo.source.connect(phys['ctrl'].sink),

                # Slave <- Master Signals
                # PHY -> FIFO
                phys['aw'].source.connect(self.aw_fifo.sink),
                phys['r'].source.connect(self.ar_fifo.sink),
                phys['w'].source.connect(self.w_fifo.sink),
                phys['ctrl'].source.connect(self.ctrl0_fifo.sink),

                # FIFO -> 32bit-Channels
                self.aw_fifo.source.connect(self.aw_addr_endpoint),
                self.ctrl0_fifo.source.connect(self.ctrl0_cast.sink),
                self.ctrl0_cast.source.connect(self.ctrl_endpoint0),
                self.ar_fifo.source.connect(self.ar_addr_endpoint),
                self.w_fifo.source.connect(self.w_data_endpoint),

                # 32bit-Channels -> bus
                self.bus.aw.addr.eq(self.aw_addr_endpoint.data), 
                self.bus.aw.prot.eq(self.ctrl_endpoint0.aw_prot),
                self.bus.aw.valid.eq(self.ctrl_endpoint0.aw_valid & self.aw_addr_endpoint.valid & self.ctrl_endpoint0.valid),

                self.bus.w.data.eq(self.w_data_endpoint.data),
                self.bus.w.strb.eq(self.ctrl_endpoint0.w_strb),
                self.bus.w.valid.eq(self.ctrl_endpoint0.w_valid & self.w_data_endpoint.valid & self.ctrl_endpoint0.valid),

                self.bus.ar.addr.eq(self.ar_addr_endpoint.data),
                self.bus.ar.prot.eq(self.ctrl_endpoint0.ar_prot),
                self.bus.ar.valid.eq(self.ctrl_endpoint0.ar_valid & self.ar_addr_endpoint.valid & self.ctrl_endpoint0.valid),
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
