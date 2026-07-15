/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau -- LAUAuBoard implementation.           *
 *                                                                               *
 * Protocol mirrored byte-for-byte from tools/uart_ctrl.ps1, ctrl/uart_ctrl.v    *
 * and ctrl/sli_lut.v. Frames:                                                   *
 *   write  : A5 57 ADDR DATA CK         -> 'K'/'E'/'N'                          *
 *   read   : A5 52 ADDR CK              -> ADDR DATA CK2                        *
 *   upload : A5 5B TARGET D[0..N-1] CK  -> 'K'/'E'                             *
 * CK drives the payload sum (after 0xA5) to 0 mod 256. The LUT-upload CK uses   *
 * (TARGET + sum(D)).                                                            *
 *                                                                               *
 *********************************************************************************/

#include "lauauboard.h"

#include <QElapsedTimer>

/****************************************************************************/
LAUAuBoard::LAUAuBoard(const QString &portName, QObject *parent) : QObject(parent), timeoutMs(1500)
{
    QString target = portName;
    if (target.isEmpty()) {
        QStringList ports = availablePorts();
        if (ports.isEmpty()) {
            errorString = QString("No serial ports found.");
            return;
        }
        target = ports.first();
    }
    openPort(target);
}

/****************************************************************************/
LAUAuBoard::~LAUAuBoard()
{
    if (serial.isOpen()) {
        serial.close();
    }
}

/****************************************************************************/
bool LAUAuBoard::openPort(const QString &name)
{
    serial.setPortName(name);
    serial.setBaudRate(QSerialPort::Baud115200);
    serial.setDataBits(QSerialPort::Data8);
    serial.setParity(QSerialPort::NoParity);
    serial.setStopBits(QSerialPort::OneStop);
    serial.setFlowControl(QSerialPort::NoFlowControl);

    if (!serial.open(QIODevice::ReadWrite)) {
        errorString = QString("Cannot open %1: %2").arg(name, serial.errorString());
        return (false);
    }
    serial.clear();
    return (true);
}

/****************************************************************************/
bool LAUAuBoard::writeFrame(const QByteArray &frame)
{
    if (!serial.isOpen()) {
        errorString = QString("Port not open.");
        return (false);
    }
    if (serial.write(frame) != frame.size()) {
        errorString = QString("Serial write failed: %1").arg(serial.errorString());
        return (false);
    }
    if (!serial.waitForBytesWritten(timeoutMs)) {
        errorString = QString("Serial write timeout.");
        return (false);
    }
    return (true);
}

/****************************************************************************/
QByteArray LAUAuBoard::readExact(int n)
{
    QByteArray buffer;
    QElapsedTimer timer;
    timer.start();
    while (buffer.size() < n) {
        int remaining = timeoutMs - (int)timer.elapsed();
        if (remaining <= 0) {
            break;
        }
        if (serial.bytesAvailable() == 0 && !serial.waitForReadyRead(remaining)) {
            break;
        }
        buffer.append(serial.readAll());
    }
    return (buffer);
}

/****************************************************************************/
int LAUAuBoard::readRegister(quint8 addr)
{
    if (!serial.isOpen()) {
        errorString = QString("Port not open.");
        return (-1);
    }

    // FLUSH ANY STALE INPUT, THEN SEND A5 52 ADDR CK
    serial.clear(QSerialPort::Input);
    quint8 ck = checksumByte(LAUAU_OP_READ + addr);
    QByteArray frame;
    frame.append((char)LAUAU_SYNC).append((char)LAUAU_OP_READ).append((char)addr).append((char)ck);
    if (!writeFrame(frame)) {
        return (-1);
    }

    // REPLY IS ADDR DATA CK2 (OR A SINGLE 'E' ON A BAD REQUEST CHECKSUM)
    QByteArray reply = readExact(3);
    if (reply.size() >= 1 && (quint8)reply.at(0) == LAUAU_ACK_ERR) {
        errorString = QString("R 0x%1 -> 'E' checksum mismatch").arg(addr, 2, 16, QChar('0'));
        return (-1);
    }
    if (reply.size() < 3) {
        errorString = QString("R 0x%1 -> no/short reply (timeout)").arg(addr, 2, 16, QChar('0'));
        return (-1);
    }
    quint8 a = (quint8)reply.at(0);
    quint8 d = (quint8)reply.at(1);
    quint8 c = (quint8)reply.at(2);
    if (a != addr) {
        errorString = QString("R 0x%1 -> addr echo mismatch (got 0x%2)").arg(addr, 2, 16, QChar('0')).arg(a, 2, 16, QChar('0'));
        return (-1);
    }
    if (((a + d + c) & 0xFF) != 0) {
        errorString = QString("R 0x%1 -> reply checksum bad").arg(addr, 2, 16, QChar('0'));
        return (-1);
    }
    return ((int)d);
}

/****************************************************************************/
bool LAUAuBoard::writeRegister(quint8 addr, quint8 value)
{
    if (!serial.isOpen()) {
        errorString = QString("Port not open.");
        return (false);
    }

    serial.clear(QSerialPort::Input);
    quint8 ck = checksumByte(LAUAU_OP_WRITE + addr + value);
    QByteArray frame;
    frame.append((char)LAUAU_SYNC).append((char)LAUAU_OP_WRITE).append((char)addr).append((char)value).append((char)ck);
    if (!writeFrame(frame)) {
        return (false);
    }

    QByteArray reply = readExact(1);
    if (reply.isEmpty()) {
        errorString = QString("W 0x%1 -> no reply (timeout)").arg(addr, 2, 16, QChar('0'));
        return (false);
    }
    switch ((quint8)reply.at(0)) {
        case LAUAU_ACK_OK:
            return (true);
        case LAUAU_ACK_ERR:
            errorString = QString("W 0x%1 -> 'E' checksum mismatch").arg(addr, 2, 16, QChar('0'));
            return (false);
        case LAUAU_ACK_NAK:
            errorString = QString("W 0x%1 -> 'N' read-only / undefined register").arg(addr, 2, 16, QChar('0'));
            return (false);
        default:
            errorString = QString("W 0x%1 -> unexpected 0x%2").arg(addr, 2, 16, QChar('0')).arg((quint8)reply.at(0), 2, 16, QChar('0'));
            return (false);
    }
}

/****************************************************************************/
bool LAUAuBoard::verifyIdentity()
{
    int id = readRegister(LAUAU_REG_ID);
    if (id < 0) {
        return (false);
    }
    if (id != LAUAU_ID_MAGIC) {
        errorString = QString("ID=0x%1 (expected 0x48) -- is the AuV2-SLI control bitstream loaded?").arg(id, 2, 16, QChar('0'));
        return (false);
    }
    return (true);
}

/****************************************************************************/
bool LAUAuBoard::uploadCorrectionTable(LAUMemoryObject toneCurve)
{
    return (uploadCorrectionTable(correctionTable(toneCurve)));
}

/****************************************************************************/
bool LAUAuBoard::uploadIdentityCorrection()
{
    return (uploadCorrectionTable(identityTable()));
}

/****************************************************************************/
bool LAUAuBoard::uploadCorrectionTable(const QByteArray &table256)
{
    if (table256.size() != 256) {
        errorString = QString("Correction table must be exactly 256 bytes (got %1).").arg(table256.size());
        return (false);
    }
    return (uploadPatternTable(table256, LAUAU_TARGET_CORR));
}

/****************************************************************************/
bool LAUAuBoard::uploadPatternTable(const QByteArray &data, quint8 target)
{
    if (!serial.isOpen()) {
        errorString = QString("Port not open.");
        return (false);
    }

    // VALIDATE THE PAYLOAD LENGTH AGAINST THE TARGET
    int expect = (target == LAUAU_TARGET_LUT) ? 720 : (target == LAUAU_TARGET_LUT_V) ? 1280 : (target == LAUAU_TARGET_CORR) ? 256 : 0;
    if (expect == 0) {
        errorString = QString("Unknown upload target 0x%1.").arg(target, 2, 16, QChar('0'));
        return (false);
    }
    if (data.size() != expect) {
        errorString = QString("Target 0x%1 needs exactly %2 bytes (got %3).").arg(target, 2, 16, QChar('0')).arg(expect).arg(data.size());
        return (false);
    }

    // CK MAKES (TARGET + sum(D) + CK) == 0 (mod 256)
    int sum = target;
    for (int n = 0; n < data.size(); n++) {
        sum += (quint8)data.at(n);
    }
    quint8 ck = checksumByte(sum);

    QByteArray frame;
    frame.append((char)LAUAU_SYNC).append((char)LAUAU_OP_LUT).append((char)target);
    frame.append(data);
    frame.append((char)ck);

    serial.clear(QSerialPort::Input);
    if (!writeFrame(frame)) {
        return (false);
    }

    QByteArray reply = readExact(1);
    if (reply.isEmpty()) {
        errorString = QString("Upload (target 0x%1) -> no ack (timeout)").arg(target, 2, 16, QChar('0'));
        return (false);
    }
    if ((quint8)reply.at(0) == LAUAU_ACK_OK) {
        return (true);
    }
    if ((quint8)reply.at(0) == LAUAU_ACK_ERR) {
        errorString = QString("Upload (target 0x%1) -> 'E' checksum/target error; table unchanged.").arg(target, 2, 16, QChar('0'));
    } else {
        errorString = QString("Upload (target 0x%1) -> unexpected 0x%2").arg(target, 2, 16, QChar('0')).arg((quint8)reply.at(0), 2, 16, QChar('0'));
    }
    return (false);
}

/****************************************************************************/
QByteArray LAUAuBoard::readTable(quint8 target)
{
    if (!serial.isOpen()) {
        errorString = QString("Port not open.");
        return (QByteArray());
    }

    // PAYLOAD LENGTH IS IMPLIED BY THE TARGET (same mapping as the FPGA's uart_ctrl)
    int expect = (target == LAUAU_TARGET_LUT)      ? 720
               : (target == LAUAU_TARGET_LUT_V)    ? 1280
               : (target == LAUAU_TARGET_CORR)     ? 256
               : (target == LAUAU_TARGET_EDID)     ? 256
               : (target == LAUAU_TARGET_CAM_LINE) ? LAUAU_CAM_LINE_LEN
               : 0;
    if (expect == 0) {
        errorString = QString("Unknown read-table target 0x%1.").arg(target, 2, 16, QChar('0'));
        return (QByteArray());
    }

    // REQUEST: A5 72 TARGET CK   with (0x72 + TARGET + CK) == 0 (mod 256)
    serial.clear(QSerialPort::Input);
    quint8 ck = checksumByte(LAUAU_OP_LUT_RD + target);
    QByteArray frame;
    frame.append((char)LAUAU_SYNC).append((char)LAUAU_OP_LUT_RD).append((char)target).append((char)ck);
    if (!writeFrame(frame)) {
        return (QByteArray());
    }

    // REPLY: TARGET D[0..expect-1] CK2  with (TARGET + sum(D) + CK2) == 0 (mod 256).
    // The reply shares the UART with status telemetry, so it may be preceded (or
    // followed) by a partial status line -- all printable ASCII / CR / LF, never a
    // TARGET value (0x00/0x01/0x02). Read incrementally and lock onto the first
    // TARGET byte that begins a checksum-valid frame; return as soon as one is found.
    const int need = expect + 2;
    QByteArray buf;
    QElapsedTimer timer;
    timer.start();
    while (timer.elapsed() < timeoutMs) {
        if (serial.bytesAvailable() == 0 && !serial.waitForReadyRead(timeoutMs - (int)timer.elapsed())) {
            break;
        }
        buf.append(serial.readAll());
        for (int i = 0; i + need <= buf.size(); i++) {
            if ((quint8)buf.at(i) != target) {
                continue;                       // skip stray / status bytes
            }
            int sum = 0;
            for (int k = 0; k < need; k++) {
                sum += (quint8)buf.at(i + k);
            }
            if ((sum & 0xFF) == 0) {
                return (buf.mid(i + 1, expect)); // TARGET + N + CK2 all check out
            }
        }
    }
    errorString = QString("read-table 0x%1 -> no valid %2-byte reply (timeout)").arg(target, 2, 16, QChar('0')).arg(expect);
    return (QByteArray());
}

/****************************************************************************/
bool LAUAuBoard::setSLIControl(bool usbEnable, bool rEnable, bool gEnable, bool bEnable, bool horizontalOrient)
{
    quint8 v = (quint8)((usbEnable ? 0x80 : 0x00) | (rEnable ? 0x08 : 0x00) | (gEnable ? 0x04 : 0x00) | (bEnable ? 0x02 : 0x00) | (horizontalOrient ? 0x01 : 0x00));
    return (writeRegister(LAUAU_REG_SLICTRL, v));
}

/****************************************************************************/
QByteArray LAUAuBoard::correctionTable(LAUMemoryObject toneCurve)
{
    QByteArray out(256, (char)0);

    // EXPECT A 256(ish)-ENTRY, SINGLE-CHANNEL, FLOAT (0..1) TONE-CORRECTION CURVE.
    // (LAUToneCorrectionWidget::toneCorrectionCurve() returns exactly this.)
    if (toneCurve.isValid() && toneCurve.depth() == sizeof(float) && toneCurve.colors() == 1 && toneCurve.height() == 1) {
        int n = (int)toneCurve.width();
        const float *src = (const float *)toneCurve.constPointer();
        for (int g = 0; g < 256; g++) {
            float v;
            if (n == 256) {
                v = src[g];
            } else if (n > 1) {
                // RESAMPLE A NON-256-WIDE CURVE ONTO THE 8-BIT GRID
                int idx = qBound(0, (int)qRound((double)g / 255.0 * (n - 1)), n - 1);
                v = src[idx];
            } else {
                v = (float)g / 255.0f;
            }
            int b = (int)qRound(v * 255.0f);
            out[g] = (char)(quint8)qBound(0, b, 255);
        }
    } else {
        // FALL BACK TO IDENTITY ON A MALFORMED CURVE
        for (int g = 0; g < 256; g++) {
            out[g] = (char)(quint8)g;
        }
    }
    return (out);
}

/****************************************************************************/
QByteArray LAUAuBoard::identityTable()
{
    QByteArray out(256, (char)0);
    for (int g = 0; g < 256; g++) {
        out[g] = (char)(quint8)g;
    }
    return (out);
}

/****************************************************************************/
QStringList LAUAuBoard::availablePorts()
{
    QStringList ftdi, others;
    const QList<QSerialPortInfo> ports = QSerialPortInfo::availablePorts();
    for (const QSerialPortInfo &info : ports) {
        bool isFtdi = (info.vendorIdentifier() == 0x0403) ||
                      info.description().contains(QString("FT2232"), Qt::CaseInsensitive) ||
                      info.manufacturer().contains(QString("FTDI"), Qt::CaseInsensitive);
        if (isFtdi) {
            ftdi << info.portName();
        } else {
            others << info.portName();
        }
    }
    return (ftdi + others);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
// PYTHON 1300 camera.
//
// A sensor SPI transaction is a 9-bit address + 16-bit data, which does not fit the
// board's 1-byte register model. So we stage the operands across registers 0x30..0x33,
// fire it with 0x34, poll 0x34 until done, and collect the result from 0x35/0x36.
//
// Every one of these works with NO sensor clock running and no configuration at all --
// the sensor's SPI is asynchronous to its system clock. That is precisely why the Au V2
// can talk to the sensor even though it can never receive its LVDS.
/****************************************************************************/

bool LAUAuBoard::cameraSpiTransact(bool isWrite, quint16 sensorReg, quint16 wdata, quint16 *rdata)
{
    if (sensorReg > 0x1FF) {
        errorString = QString("camera: sensor register %1 exceeds 9 bits").arg(sensorReg);
        return (false);
    }

    // STAGE THE OPERANDS
    if (!writeRegister(LAUAU_REG_CAM_ADDR, (quint8)(sensorReg & 0xFF))) {
        return (false);
    }
    quint8 cmd = (quint8)((isWrite ? 0x80 : 0x00) | ((sensorReg >> 8) & 0x01));
    if (!writeRegister(LAUAU_REG_CAM_CMD, cmd)) {
        return (false);
    }
    if (isWrite) {
        if (!writeRegister(LAUAU_REG_CAM_WDATA_L, (quint8)(wdata & 0xFF))) {
            return (false);
        }
        if (!writeRegister(LAUAU_REG_CAM_WDATA_H, (quint8)(wdata >> 8))) {
            return (false);
        }
    }

    // FIRE
    if (!writeRegister(LAUAU_REG_CAM_GO, 0x01)) {
        return (false);
    }

    // POLL. A transaction is ~30 us at 1 MHz sck; a single UART frame is ~400 us, so this
    // is almost always done on the first read. The loop exists for the pathological case.
    for (int i = 0; i < 20; i++) {
        int st = readRegister(LAUAU_REG_CAM_GO);
        if (st < 0) {
            return (false);
        }
        if (!(st & LAUAU_CAM_GO_BUSY) && (st & LAUAU_CAM_GO_DONE)) {
            if (rdata) {
                int lo = readRegister(LAUAU_REG_CAM_RDATA_L);
                int hi = readRegister(LAUAU_REG_CAM_RDATA_H);
                if (lo < 0 || hi < 0) {
                    return (false);
                }
                *rdata = (quint16)((hi << 8) | lo);
            }
            return (true);
        }
    }

    errorString = QString("camera: SPI transaction to sensor reg %1 never completed").arg(sensorReg);
    return (false);
}

int LAUAuBoard::cameraSpiRead(quint16 sensorReg)
{
    quint16 v = 0;
    if (!cameraSpiTransact(false, sensorReg, 0, &v)) {
        return (-1);
    }
    return ((int)v);
}

bool LAUAuBoard::cameraSpiWrite(quint16 sensorReg, quint16 value)
{
    return (cameraSpiTransact(true, sensorReg, value, nullptr));
}

bool LAUAuBoard::cameraSetReset(bool released)
{
    // reg 0x37 = {7:reset_n, 2..0:trigger}. Preserve the triggers.
    int cur = readRegister(LAUAU_REG_CAM_GPIO);
    if (cur < 0) {
        return (false);
    }
    quint8 v = (quint8)(cur & 0x07);
    if (released) {
        v |= 0x80;
    }
    return (writeRegister(LAUAU_REG_CAM_GPIO, v));
}

bool LAUAuBoard::cameraSetTriggers(quint8 mask3)
{
    int cur = readRegister(LAUAU_REG_CAM_GPIO);
    if (cur < 0) {
        return (false);
    }
    quint8 v = (quint8)((cur & 0x80) | (mask3 & 0x07));
    return (writeRegister(LAUAU_REG_CAM_GPIO, v));
}

int LAUAuBoard::cameraMonitorPins()
{
    int v = readRegister(LAUAU_REG_CAM_MON);
    if (v < 0) {
        return (-1);
    }
    return (v & 0x03);
}

bool LAUAuBoard::verifyCameraChipId()
{
    // The sensor comes out of FPGA configuration HELD IN RESET (cam_gpio resets to 0x00,
    // and the board fits a 10k pulldown on reset_n). Nothing answers on SPI until it is
    // released -- so a failure here with reset still asserted is not a board fault.
    if (!cameraSetReset(true)) {
        return (false);
    }

    int id = cameraSpiRead(PYTHON_REG_CHIP_ID);
    if (id < 0) {
        return (false);
    }
    if (id != PYTHON_CHIP_ID) {
        // 0xA1A0 is 0x50D0 shifted left one bit -- the signature of sampling miso on the
        // wrong sck edge. It is a logic bug, NOT a bad board. See CAMERA_SENSOR_PROTOCOL.md 1.1.
        errorString = QString("camera: chip ID 0x%1, expected 0x%2%3")
                          .arg(id, 4, 16, QChar('0'))
                          .arg(PYTHON_CHIP_ID, 4, 16, QChar('0'))
                          .arg(id == 0xA1A0 ? "  (== ID << 1: miso sampled on the wrong edge)" : "");
        return (false);
    }
    return (true);
}
