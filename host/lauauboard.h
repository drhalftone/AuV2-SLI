/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 * All rights reserved.                                                          *
 *                                                                               *
 * LAUAuBoard -- host-side USB-serial interface to the Alchitry Au V2 AuV2-SLI   *
 * control block (FT2232 channel B, UART 8N1 @ 115200). Speaks the 0xA5 framed   *
 * protocol described in CONTROL_PARAMS.md / ctrl/uart_ctrl.v / ctrl/sli_lut.v.  *
 *                                                                               *
 * This is the clean replacement for the old Mojo-board uploader: instead of     *
 * baking a linearised sinusoid into a pattern table, the host builds an 8-bit   *
 * intensity CORRECTION table and uploads it (TARGET 0x02); the FPGA applies it  *
 * on the fly (out = corr[cos_sample]) to render the linearised sinusoid.        *
 *                                                                               *
 *********************************************************************************/

#ifndef LAUAUBOARD_H
#define LAUAUBOARD_H

#include <QObject>
#include <QString>
#include <QStringList>
#include <QByteArray>
#include <QSerialPort>
#include <QSerialPortInfo>

#include "laumemoryobject.h"

// ---- protocol constants (see CONTROL_PARAMS.md) -------------------------------
#define LAUAU_SYNC      0xA5
#define LAUAU_OP_WRITE  0x57   // 'W'
#define LAUAU_OP_READ   0x52   // 'R'
#define LAUAU_OP_LUT    0x5B   // table upload
#define LAUAU_OP_LUT_RD 0x72   // table readback ('r')
#define LAUAU_ACK_OK    0x4B   // 'K'
#define LAUAU_ACK_ERR   0x45   // 'E'
#define LAUAU_ACK_NAK   0x4E   // 'N' (read-only / undefined register)

// upload targets for op 0x5B
#define LAUAU_TARGET_LUT    0x00   // 720-byte pattern cosine (top-down / row)
#define LAUAU_TARGET_LUT_V  0x01   // 1280-byte pattern cosine (side-to-side / col)
#define LAUAU_TARGET_CORR   0x02   // 256-byte 8-bit intensity correction (shared)

// control / status register addresses
#define LAUAU_REG_ID        0x00   // const 0x48 ('H')
#define LAUAU_REG_VERSION   0x01
#define LAUAU_REG_STATUS    0x02
#define LAUAU_REG_FLAGS     0x06   // {1:usb_sw_en, 0:lut_loaded}
#define LAUAU_REG_PINS      0x10   // {eff_sw[3:0], phys_sw[3:0]} = active vs physical R/G/B/orient
#define LAUAU_REG_SLICTRL   0x13   // {7:sw_en, 3:R_en, 2:G_en, 1:B_en, 0:orient}
#define LAUAU_ID_MAGIC      0x48

// ---- PYTHON 1300 camera (see CAMERA_SENSOR_PROTOCOL.md) -----------------------
// A sensor SPI transaction is a 9-bit address + 16-bit data, which does not fit the
// 1-byte register model. The operands are staged across these registers, then fired.
#define LAUAU_REG_CAM_ADDR    0x30   // W  sensor addr[7:0]
#define LAUAU_REG_CAM_CMD     0x31   // W  {7:rw (1=write), 0:addr[8]}
#define LAUAU_REG_CAM_WDATA_L 0x32   // W  wdata[7:0]
#define LAUAU_REG_CAM_WDATA_H 0x33   // W  wdata[15:8]
#define LAUAU_REG_CAM_GO      0x34   // W  fire (any value) / R {7:busy, 6:done}
#define LAUAU_REG_CAM_RDATA_L 0x35   // R  rdata[7:0]
#define LAUAU_REG_CAM_RDATA_H 0x36   // R  rdata[15:8]
#define LAUAU_REG_CAM_GPIO    0x37   // RW {7:reset_n, 2..0:trigger[2:0]}
#define LAUAU_REG_CAM_MON     0x38   // R  {1..0:monitor[1:0]}

#define LAUAU_CAM_GO_BUSY     0x80
#define LAUAU_CAM_GO_DONE     0x40

// Sensor-side registers worth naming (datasheet Table 28)
#define PYTHON_REG_CHIP_ID    0      // read-only status; MUST read 0x50D0
#define PYTHON_REG_TRAINING   116    // R/W, default 0x03A6 -- a safe write/read-back target
#define PYTHON_REG_PLL_LOCK   24     // [0] = PLL locked
#define PYTHON_REG_LVDS_PWR   112    // LVDS driver power. DO NOT WRITE ON AN Au BUILD --
                                     // dout0 lands on the Au's 1.35 V bank 15, which is not
                                     // 3.3 V tolerant. See CAMERA_IO_MAP.md section 8.2.
#define PYTHON_CHIP_ID        0x50D0

class LAUAuBoard : public QObject
{
    Q_OBJECT

public:
    // Open the board. If portName is empty, the first candidate FT2232 port is used
    // (or the only available port). Check isValid()/error() afterwards.
    explicit LAUAuBoard(const QString &portName = QString(), QObject *parent = nullptr);
    ~LAUAuBoard();

    bool isValid() const
    {
        return (serial.isOpen());
    }

    QString error() const
    {
        return (errorString);
    }

    QString portName() const
    {
        return (serial.portName());
    }

    // --- register access (blocking, ~timeoutMs each) ---------------------------
    // readRegister returns 0..255, or -1 on timeout / checksum / echo error.
    int  readRegister(quint8 addr);
    // writeRegister returns true on 'K'; false on 'E'/'N'/timeout (see error()).
    bool writeRegister(quint8 addr, quint8 value);

    // confirm we are talking to the right bitstream (ID register == 0x48).
    bool verifyIdentity();

    // --- correction-table upload (TARGET 0x02, 256 bytes) ----------------------
    // Build the table from a 256-entry float tone-correction curve (the output of
    // LAUToneCorrectionWidget::toneCorrectionCurve()) and upload it.
    bool uploadCorrectionTable(LAUMemoryObject toneCurve);
    // Upload a pre-built 256-byte table directly.
    bool uploadCorrectionTable(const QByteArray &table256);
    // Reset linearisation to identity (corr[i] = i).
    bool uploadIdentityCorrection();

    // --- pattern-table upload (TARGET 0x00 / 0x01) -----------------------------
    // data must be exactly 720 (LUT) or 1280 (LUT_V) bytes.
    bool uploadPatternTable(const QByteArray &data, quint8 target);

    // --- table readback (op 0x72) ----------------------------------------------
    // Read a target table back from the FPGA (256/720/1280 bytes by target). Returns
    // the bytes on success, or an empty QByteArray on timeout/checksum/target error
    // (see error()). Lets you verify an upload landed byte-for-byte.
    QByteArray readTable(quint8 target);
    // Convenience: read the 256-byte correction table (TARGET 0x02).
    QByteArray readCorrectionTable()
    {
        return (readTable(LAUAU_TARGET_CORR));
    }

    // --- SLI control register 0x13 convenience ---------------------------------
    // usbEnable=true makes USB drive the SLI controls (overrides the physical
    // switches). horizontalOrient: false = vertical stripes (cols), true = rows.
    bool setSLIControl(bool usbEnable, bool rEnable, bool gEnable, bool bEnable, bool horizontalOrient);

    // ---- PYTHON 1300 camera ----------------------------------------------------
    // The sensor's SPI is asynchronous to its system clock: these work with NO sensor
    // clock running, before any configuration. That is what makes Au V2 bring-up possible.

    // Read a 16-bit sensor register. Returns -1 on error.
    int  cameraSpiRead(quint16 sensorReg);

    // Write a 16-bit sensor register.
    bool cameraSpiWrite(quint16 sensorReg, quint16 value);

    // Release (or assert) the sensor's reset. It comes out of FPGA config HELD IN RESET.
    bool cameraSetReset(bool released);

    // Drive trigger[2:0]. Keeps reset_n at its current value.
    bool cameraSetTriggers(quint8 mask3);

    // Read monitor[1:0]. Returns -1 on error.
    int  cameraMonitorPins();

    // THE HARDWARE GATE. Reads sensor register 0 and checks it against 0x50D0.
    // A pass proves the power tree came up correctly sequenced, the DF40 pin map and the
    // stack pass-through are right, reset_n released, and the SPI path works -- in one
    // transaction. See CAMERA_RTL_PLAN.md milestone 5.
    bool verifyCameraChipId();

    // --- static builders -------------------------------------------------------
    // 256 bytes from a 256-entry float (0..1) tone-correction curve: b[g] = round(255*tcc[g]).
    static QByteArray correctionTable(LAUMemoryObject toneCurve);
    // 256 bytes, identity (0,1,...,255).
    static QByteArray identityTable();

    // candidate ports (FTDI / FT2232 first, then everything else).
    static QStringList availablePorts();

signals:
    void emitError(QString message);

private:
    QSerialPort serial;
    QString     errorString;
    int         timeoutMs;

    // open the named port with 115200 8N1, no flow control.
    // Stage operands -> fire -> poll -> collect. Shared by cameraSpiRead/Write.
    bool cameraSpiTransact(bool isWrite, quint16 sensorReg, quint16 wdata, quint16 *rdata);

    bool openPort(const QString &portName);
    // write the whole frame, flushing.
    bool writeFrame(const QByteArray &frame);
    // block until exactly n bytes are read or timeout; returns what was read.
    QByteArray readExact(int n);
    // checksum byte that drives a running payload sum to 0 mod 256.
    static quint8 checksumByte(int runningSum)
    {
        return (quint8)((256 - (runningSum & 0xFF)) & 0xFF);
    }
};

#endif // LAUAUBOARD_H
