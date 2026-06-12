#ifndef LAUBASLERUSBCAMERA_H
#define LAUBASLERUSBCAMERA_H

#include <QTime>
#include <QList>
#include <QTimer>
#include <QtCore>
#include <QDebug>
#include <QImage>
#include <QString>
#include <QObject>
#include <QMessageBox>
#include <QApplication>
#include <QHostAddress>

#include "laumemoryobject.h"
#if defined(Q_OS_WIN)
#include <pylon/PylonIncludes.h>
#include <pylon/usb/BaslerUsbInstantCamera.h>
#elif defined(Q_OS_MAC)
#include <PylonIncludes.h>
#include <usb/BaslerUsbInstantCamera.h>
#elif defined(Q_OS_LINUX)
#include <Base/GCString.h>
#include <pylon/PylonIncludes.h>
#include <pylon/usb/BaslerUsbInstantCamera.h>
#endif

#define USE_USB
#define NUMBASLERUSBHEADERFRAMES 1

class LAUBaslerUSBCamera : public QObject
{
    Q_OBJECT

public:
    explicit LAUBaslerUSBCamera(int frms, int btcs, QObject *parent = nullptr);
    ~LAUBaslerUSBCamera();

    bool isValid() const
    {
        return (isConnected);
    }

    void enableCalibration(bool state)
    {
        enableCalibrationFlag = state;
    }

    void disableCalibration(bool state)
    {
        enableCalibrationFlag = !state;
    }

    void enableHDMITriggering(bool state)
    {
        enableHDMITriggeringFlag = state;
    }

    void disableHDMITriggering(bool state)
    {
        enableHDMITriggeringFlag = !state;
    }

    void enableSensorResponse(bool state)
    {
        enableMeasureSensorResponseFlag = state;
    }

    void disableSensorResponse(bool state)
    {
        enableMeasureSensorResponseFlag = !state;
    }

    unsigned short maxIntensityValue() const
    {
        return ((unsigned short)(0x01 << bitsPerPixel));
    }

    QString error() const
    {
        return (errorString);
    }

    QString make() const
    {
        return (makeString);
    }

    QString model() const
    {
        return (modelString);
    }

    QString serial() const
    {
        return (serialString);
    }

    unsigned int width() const
    {
        return (numCols);
    }

    unsigned int height() const
    {
        return (numRows);
    }

    unsigned int frames() const
    {
        return (numFrms);
    }

    LAUMemoryObject memoryObject() const
    {
        return (LAUMemoryObject(width(), height(), 1, sizeof(unsigned short), numFrms));
    }

    bool reset();
    static bool haltVideoFlag;

protected:
    virtual void updateBuffer(LAUMemoryObject buffer);

public slots:
    void onUpdateExposure(int microseconds);
    void onUpdateBuffer(LAUMemoryObject buffer)
    {
        // CALL COMMANDS TO RECORD VIDEO TO BUFFER
        updateBuffer(buffer);

        // SEND BUFFER BACK TO THE USER
        emit emitBuffer(buffer);
    }

private:
    static bool libraryInitializedFlag;

    unsigned int numRows;
    unsigned int numCols;
    unsigned int numFrms;
    unsigned int numBtcs;

    unsigned short bitsPerPixel;
    bool isConnected;
    bool enableCalibrationFlag;
    bool enableHDMITriggeringFlag;
    bool enableMeasureSensorResponseFlag;

    QString makeString;
    QString errorString;
    QString modelString;
    QString serialString;

    // DECLARE POINTERS TO PRIMESENSE SENSOR OBJECTS
    unsigned int numAvailableCameras;
    Pylon::CBaslerUsbInstantCamera *camera;

    QStringList cameraList();

    void debayer(unsigned char *otBuffer, unsigned char *inBuffer, unsigned int rows, unsigned int cols, unsigned int step);
    void unpack10Bits(unsigned char *toBuffer, unsigned char *fmBuffer);
    bool setSynchronization();
    bool disconnectFromHost();
    bool connectToHost(QString);

signals:
    void emitError(QString);
    void emitTriggerVideo();
    void emitTriggerVideo(int start, int count);
    void emitDisplayFrame(unsigned int index);
    void emitBuffer(LAUMemoryObject);
    void emitMeanPixel(unsigned int frm, unsigned int mean);
};
#endif // LAUBASLERUSBCAMERA_H
