`timescale 1ns / 1ps
//=============================================================================
// cam_cds_rom.v - the PYTHON 1300 CDS / sequencer-program upload.
//
// Generated from Avnet/hdl, Projects/embv_p1300c/software/sw_repository/
// sw_services/onsemi_python_sw_v3_3/src/onsemi_python_sw.c, table
// vita_cds_seq (104 entries), applied by onsemi_python_sensor_cds(). Their
// PYTHON demo app calls it as the LAST init step, after stream-on:
//
//     onsemi_python_sensor_initialize(..., SENSOR_INIT_ENABLE,   0);
//     onsemi_python_sensor_initialize(..., SENSOR_INIT_STREAMON, 0);
//     onsemi_python_sensor_cds(pdemo->pPython_receiver, 0);
//
// We ran the first two and never the third. 91 of the 104 entries are
// registers 384-474 -- the sensor's CDS / sequencer TIMING PROGRAM -- so the
// pixel array has been running on power-on defaults this whole time. The
// datasheet (p17-18) says the full required upload is available only under NDA
// and that different "reserved" settings "may cause the sensor to malfunction";
// this table is that upload, published in Avnet's PYTHON project.
//
// It also rewrites 41/42/43/65/72 with values that DIFFER from the ones our
// boot ROM uses (42 = 0x4113 vs 0x0011, 72 = 0x0017 vs 0x2227) -- ours came
// from the VITA sequences in the same file.
//
// NOTE entry 0 is 192 = 0x0800 (sequencer OFF) and the last is 192 = 0x0801
// (back ON). Both would clear our mode bits, so the caller MUST re-apply them
// on any write to register 192 -- that exact mistake (a bare 0x0801 clearing
// triggered_mode) already cost a day once.
//=============================================================================
module cam_cds_rom (
    input  wire [6:0]  idx,
    output reg  [8:0]  addr,
    output reg  [15:0] data
);
    always @(*) begin
        case (idx)
        7'd0  : begin addr = 9'd192; data = 16'h0800; end
        7'd1  : begin addr = 9'd204; data = 16'h01E3; end
        7'd2  : begin addr = 9'd65 ; data = 16'h288B; end
        7'd3  : begin addr = 9'd41 ; data = 16'h085F; end
        7'd4  : begin addr = 9'd42 ; data = 16'h4113; end
        7'd5  : begin addr = 9'd43 ; data = 16'h0008; end
        7'd6  : begin addr = 9'd72 ; data = 16'h0017; end
        7'd7  : begin addr = 9'd384; data = 16'hC800; end
        7'd8  : begin addr = 9'd385; data = 16'hFB1F; end
        7'd9  : begin addr = 9'd386; data = 16'hFB1F; end
        7'd10 : begin addr = 9'd387; data = 16'hFB12; end
        7'd11 : begin addr = 9'd388; data = 16'hF903; end
        7'd12 : begin addr = 9'd389; data = 16'hF802; end
        7'd13 : begin addr = 9'd390; data = 16'hF30F; end
        7'd14 : begin addr = 9'd391; data = 16'hF30F; end
        7'd15 : begin addr = 9'd392; data = 16'hF30F; end
        7'd16 : begin addr = 9'd393; data = 16'hF30A; end
        7'd17 : begin addr = 9'd394; data = 16'hF101; end
        7'd18 : begin addr = 9'd395; data = 16'hF00A; end
        7'd19 : begin addr = 9'd396; data = 16'hF24B; end
        7'd20 : begin addr = 9'd397; data = 16'hF226; end
        7'd21 : begin addr = 9'd398; data = 16'hF001; end
        7'd22 : begin addr = 9'd399; data = 16'hF402; end
        7'd23 : begin addr = 9'd400; data = 16'hF001; end
        7'd24 : begin addr = 9'd401; data = 16'hF402; end
        7'd25 : begin addr = 9'd402; data = 16'hF001; end
        7'd26 : begin addr = 9'd403; data = 16'hF401; end
        7'd27 : begin addr = 9'd404; data = 16'hF007; end
        7'd28 : begin addr = 9'd405; data = 16'hF20F; end
        7'd29 : begin addr = 9'd406; data = 16'hF20F; end
        7'd30 : begin addr = 9'd407; data = 16'hF202; end
        7'd31 : begin addr = 9'd408; data = 16'hF006; end
        7'd32 : begin addr = 9'd409; data = 16'hEC02; end
        7'd33 : begin addr = 9'd410; data = 16'hE801; end
        7'd34 : begin addr = 9'd411; data = 16'hEC02; end
        7'd35 : begin addr = 9'd412; data = 16'hE801; end
        7'd36 : begin addr = 9'd413; data = 16'hEC02; end
        7'd37 : begin addr = 9'd414; data = 16'hC801; end
        7'd38 : begin addr = 9'd415; data = 16'hC800; end
        7'd39 : begin addr = 9'd216; data = 16'h7F00; end
        7'd40 : begin addr = 9'd416; data = 16'hC800; end
        7'd41 : begin addr = 9'd417; data = 16'hCC02; end
        7'd42 : begin addr = 9'd418; data = 16'hC801; end
        7'd43 : begin addr = 9'd419; data = 16'hCC02; end
        7'd44 : begin addr = 9'd420; data = 16'hC801; end
        7'd45 : begin addr = 9'd421; data = 16'hCC02; end
        7'd46 : begin addr = 9'd422; data = 16'hC806; end
        7'd47 : begin addr = 9'd423; data = 16'hC800; end
        7'd48 : begin addr = 9'd219; data = 16'h0020; end
        7'd49 : begin addr = 9'd424; data = 16'h0030; end
        7'd50 : begin addr = 9'd425; data = 16'h2076; end
        7'd51 : begin addr = 9'd426; data = 16'h2071; end
        7'd52 : begin addr = 9'd427; data = 16'h0071; end
        7'd53 : begin addr = 9'd428; data = 16'h107F; end
        7'd54 : begin addr = 9'd429; data = 16'h1072; end
        7'd55 : begin addr = 9'd430; data = 16'h1074; end
        7'd56 : begin addr = 9'd431; data = 16'h0076; end
        7'd57 : begin addr = 9'd432; data = 16'h0031; end
        7'd58 : begin addr = 9'd433; data = 16'h21BB; end
        7'd59 : begin addr = 9'd434; data = 16'h20B1; end
        7'd60 : begin addr = 9'd435; data = 16'h00B1; end
        7'd61 : begin addr = 9'd436; data = 16'h10BF; end
        7'd62 : begin addr = 9'd437; data = 16'h10B2; end
        7'd63 : begin addr = 9'd438; data = 16'h10B4; end
        7'd64 : begin addr = 9'd439; data = 16'h00B1; end
        7'd65 : begin addr = 9'd440; data = 16'h0030; end
        7'd66 : begin addr = 9'd441; data = 16'h0030; end
        7'd67 : begin addr = 9'd442; data = 16'h217B; end
        7'd68 : begin addr = 9'd443; data = 16'h2071; end
        7'd69 : begin addr = 9'd444; data = 16'h2071; end
        7'd70 : begin addr = 9'd445; data = 16'h0071; end
        7'd71 : begin addr = 9'd446; data = 16'h107F; end
        7'd72 : begin addr = 9'd447; data = 16'h1072; end
        7'd73 : begin addr = 9'd448; data = 16'h1074; end
        7'd74 : begin addr = 9'd449; data = 16'h0076; end
        7'd75 : begin addr = 9'd450; data = 16'h0031; end
        7'd76 : begin addr = 9'd451; data = 16'h20B6; end
        7'd77 : begin addr = 9'd452; data = 16'h00B1; end
        7'd78 : begin addr = 9'd453; data = 16'h10BF; end
        7'd79 : begin addr = 9'd454; data = 16'h10B2; end
        7'd80 : begin addr = 9'd455; data = 16'h10B4; end
        7'd81 : begin addr = 9'd456; data = 16'h00B1; end
        7'd82 : begin addr = 9'd457; data = 16'h0030; end
        7'd83 : begin addr = 9'd220; data = 16'h3928; end
        7'd84 : begin addr = 9'd458; data = 16'h0030; end
        7'd85 : begin addr = 9'd459; data = 16'h20F3; end
        7'd86 : begin addr = 9'd460; data = 16'h2071; end
        7'd87 : begin addr = 9'd461; data = 16'h0071; end
        7'd88 : begin addr = 9'd462; data = 16'h0179; end
        7'd89 : begin addr = 9'd463; data = 16'h0078; end
        7'd90 : begin addr = 9'd464; data = 16'h1074; end
        7'd91 : begin addr = 9'd465; data = 16'h0076; end
        7'd92 : begin addr = 9'd466; data = 16'h0031; end
        7'd93 : begin addr = 9'd467; data = 16'h21BD; end
        7'd94 : begin addr = 9'd468; data = 16'h20B1; end
        7'd95 : begin addr = 9'd469; data = 16'h00B1; end
        7'd96 : begin addr = 9'd470; data = 16'h10BF; end
        7'd97 : begin addr = 9'd471; data = 16'h10B2; end
        7'd98 : begin addr = 9'd472; data = 16'h10B4; end
        7'd99 : begin addr = 9'd473; data = 16'h00B1; end
        7'd100: begin addr = 9'd474; data = 16'h0030; end
        7'd101: begin addr = 9'd221; data = 16'h624A; end
        7'd102: begin addr = 9'd222; data = 16'h624A; end
        7'd103: begin addr = 9'd192; data = 16'h0801; end
        default: begin addr = 9'd0; data = 16'h0000; end
        endcase
    end
endmodule
