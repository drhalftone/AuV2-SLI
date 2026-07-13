// mode_table.vh -- curated generatable modes for offline mode_select.
//
// PRIORITY-SORTED: index 0 = highest priority (refresh desc, then resolution
// desc), index 12 = lowest = the 640x480@60 failsafe. Selection is therefore a
// simple "lowest set index in the supported mask, else failsafe".
//
// All entries <= 85 MHz pixel clock (see README "Phase C" + the 85 MHz ceiling).
// Fields: HACT VACT REFR(Hz) PCLK(kHz) HFP HSYNC HBP VFP VSYNC VBP HPOL VPOL
//   pol: 1 = positive sync, 0 = negative.  DMT where it exists, else CVT-RB.
//
// Included inside an initial block in mode_select.v.
`define MROW(i,ha,va,rf,pk,hfp,hs,hbp,vfp,vs,vbp,hp,vp) \
    T_HACT[i]=ha;  T_VACT[i]=va;  T_REFR[i]=rf;  T_PCLK[i]=pk; \
    T_HFP[i]=hfp;  T_HSYNC[i]=hs; T_HBP[i]=hbp; \
    T_VFP[i]=vfp;  T_VSYNC[i]=vs; T_VBP[i]=vbp;  T_HPOL[i]=hp; T_VPOL[i]=vp;

//        idx  HACT VACT  Hz   pclkkHz HFP  HS  HBP  VFP VS VBP  Hp Vp
`MROW( 0,  800, 600, 120, 73270,  48,  32,  80,   3,  6, 27,  1, 0)  // 800x600@120  CVT-RB
`MROW( 1,  640, 480, 120, 52420,  48,  32,  80,   3,  4, 59,  1, 0)  // 640x480@120  CVT-RB
`MROW( 2, 1024, 768,  75, 78750,  16,  96, 176,   1,  3, 28,  1, 1)  // 1024x768@75  DMT
`MROW( 3,  800, 600,  75, 49500,  16,  80, 160,   1,  3, 21,  1, 1)  // 800x600@75   DMT
`MROW( 4,  640, 480,  75, 31500,  16,  64, 120,   1,  3, 16,  0, 0)  // 640x480@75   DMT
`MROW( 5, 1024, 768,  70, 75000,  24, 136, 144,   3,  6, 29,  0, 0)  // 1024x768@70  DMT
`MROW( 6,  800, 600,  72, 50000,  56, 120,  64,  37,  6, 23,  1, 1)  // 800x600@72   DMT
`MROW( 7,  640, 480,  72, 31500,  24,  40, 128,   9,  3, 28,  0, 0)  // 640x480@72   DMT
`MROW( 8, 1280, 720,  60, 74250, 110,  40, 220,   5,  5, 20,  1, 1)  // 1280x720@60  CEA/DMT
`MROW( 9, 1280, 800,  60, 71110,  48,  32,  80,   3,  6, 14,  1, 0)  // 1280x800@60  CVT-RB
`MROW(10, 1024, 768,  60, 65000,  24, 136, 160,   3,  6, 29,  0, 0)  // 1024x768@60  DMT
`MROW(11,  800, 600,  60, 40000,  40, 128,  88,   1,  4, 23,  1, 1)  // 800x600@60   DMT
`MROW(12,  640, 480,  60, 25175,  16,  96,  48,  10,  2, 33,  0, 0)  // 640x480@60   DMT (FAILSAFE / VIC 1)

// ---------------------------------------------------------------------------
// idx 13 -- ABOVE the old 85 MHz ceiling. UNDER TEST on the Au V2.
//
// 1280x1024@60 DMT: 108.000 MHz pixel -> serializer x5 = 540 MHz. The 85 MHz ceiling
// was inherited verbatim from the Mimas A7 (a -1 50T part); the Au V2 is a -2 grade
// with a higher OSERDES/BUFG ceiling (~600 MHz on the x5 clock, i.e. ~120 MHz pixel),
// so this should fit -- with only ~10% margin. If the output blacks out at 108 MHz,
// THIS is the mode to drop and the ceiling to put back.
//
// It is EXACTLY generatable: M=54 D=5 O0=10 O2=2 -> VCO 1080, pixel 108.000 MHz.
// (1680x1050@60RB, 119 MHz, has NO integer M/D/O solution under O0 = 5*O2 -- do not
// bother adding it.)
// ---------------------------------------------------------------------------
`MROW(13, 1280,1024,  60,108000,  48, 112, 248,   1,  3, 38,  1, 1)  // 1280x1024@60 DMT (108 MHz)

`undef MROW
