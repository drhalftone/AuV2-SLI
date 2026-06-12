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
#define LAUAU_REG_SLICTRL   0x13   // {7:sw_en, 3:R_en, 2:G_en, 1:B_en, 0:orient}
#define LAUAU_ID_MAGIC      0x48

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

    // --- SLI control register 0x13 convenience ---------------------------------
    // usbEnable=true makes USB drive the SLI controls (overrides the physical
    // switches). horizontalOrient: false = vertical stripes (cols), true = rows.
    bool setSLIControl(bool usbEnable, bool rEnable, bool gEnable, bool bEnable, bool horizontalOrient);

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
