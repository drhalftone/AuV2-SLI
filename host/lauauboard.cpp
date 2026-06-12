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
