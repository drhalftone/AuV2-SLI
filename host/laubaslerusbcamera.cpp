#include "laubaslerusbcamera.h"

#include <QtMath>

using namespace Pylon;
using namespace Basler_UsbCameraParams;

bool LAUBaslerUSBCamera::libraryInitializedFlag = false;
bool LAUBaslerUSBCamera::haltVideoFlag = false;

#ifdef RECORDSTATISTICS
QFile file;
QTextStream stream;
#endif

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
LAUBaslerUSBCamera::LAUBaslerUSBCamera(int frms, int btcs, QObject *parent) : QObject(parent), numRows(0), numCols(0), numFrms(frms), numBtcs(btcs), bitsPerPixel(12), isConnected(false), enableCalibrationFlag(false), enableMeasureSensorResponseFlag(false), enableHDMITriggeringFlag(false), camera(nullptr)
{
    // UPDATE THE NUMBER OF FRAMES TO GET AN INTEGER NUMBER OF BATCHES
    if (numFrms % numBtcs == 0) {
        numFrms = (numFrms / numBtcs) * numBtcs;
    } else {
        numFrms = (numFrms / numBtcs + 1) * numBtcs;
    }

    // INITIALIZE CAMERA LIBRARY AND UNLOAD IF ERROR
    if (!libraryInitializedFlag) {
        PylonInitialize();
        libraryInitializedFlag = true;
    }

    // KEEP TRYING TO FIND CAMERAS WHILE LIBRARY SEARCHES NETWORK
    errorString = QString("No cameras found.");

    // GET A LIST OF AVAILABLE CAMERAS
    QStringList availableCameralist = cameraList();
    if (numAvailableCameras) {
        // NOW SEE IF WE CAN CONNECT TO FIRST DETECTED CAMERA
        if (connectToHost(availableCameralist.first())) {
            errorString = QString();
            isConnected = true;
        } else {
            disconnectFromHost();
            isConnected = false;
        }
    }

#ifdef RECORDSTATISTICS
    if (isConnected) {
        QString filename = QStandardPaths::writableLocation(QStandardPaths::TempLocation).append("/cameraStatistics.csv");
        file.setFileName(filename);
        if (file.open(QIODevice::WriteOnly)) {
            stream.setDevice(&file);
        }
        qDebug() << filename;
    }
#endif

    // SET THE EXPOSURE TO 13600 MICROSECONDS
//#define USEOPTOMAML500
//#define USETIDLPPROJECTOR
#if defined(USETIDLPPROJECTOR)
    onUpdateExposure(16000);
#elif defined(USEOPTOMAML500)
    onUpdateExposure(1360);
#else
    onUpdateExposure(8200); //8000); //8100 1000000/120);
#endif
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
LAUBaslerUSBCamera::~LAUBaslerUSBCamera()
{
    // DISCONNECT FROM CAMERA
    if (isConnected) {
        disconnectFromHost();
    }
    PylonTerminate();

#ifdef RECORDSTATISTICS
    if (file.isOpen()) {
        file.close();
    }
#endif

    qDebug() << QString("LAUBaslerUSBCamera::~LAUBaslerUSBCamera()");
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
QStringList LAUBaslerUSBCamera::cameraList()
{
    // ONLY LOOK FOR CAMERAS SUPPORTED BY CAMERA_T.
    CDeviceInfo info;
    info.SetDeviceClass(CBaslerUsbInstantCamera::DeviceClass());

    // GET THE TRANSPORT LAYER FACTORY.
    CTlFactory &tlFactory = CTlFactory::GetInstance();

    // GET ALL ATTACHED DEVICES AND EXIT APPLICATION IF NO DEVICE IS FOUND.
    DeviceInfoList_t devices;
    numAvailableCameras = tlFactory.EnumerateDevices(devices);

    // PRINT A LIST OF THE CONNECTED CAMERAS
    QStringList stringList;
    for (unsigned int i = 0; i < numAvailableCameras; i++) {
        stringList << QString(devices[i].GetFriendlyName());
    }
    return (stringList);
}

/******************************************************************************/
/******************************************************************************/
/******************************************************************************/
bool LAUBaslerUSBCamera::connectToHost(QString hostString)
{
    // ONLY LOOK FOR CAMERAS SUPPORTED BY CAMERA_T.
    CDeviceInfo info;
    info.SetDeviceClass(CBaslerUsbInstantCamera::DeviceClass());

    // GET THE TRANSPORT LAYER FACTORY.
    CTlFactory &tlFactory = CTlFactory::GetInstance();

    // GET ALL ATTACHED DEVICES AND EXIT APPLICATION IF NO DEVICE IS FOUND.
    DeviceInfoList_t devices;
    numAvailableCameras = tlFactory.EnumerateDevices(devices);

    try {
        // PRINT A LIST OF THE CONNECTED CAMERAS
        for (unsigned int i = 0; i < numAvailableCameras; i++) {
            if (QString(devices[i].GetFriendlyName()) == hostString) {
                // CREATE AN INSTANT CAMERA OBJECT WITH THE FIRST FOUND CAMERA DEVICE MATCHING THE SPECIFIED DEVICE CLASS.
                camera = new CBaslerUsbInstantCamera(CTlFactory::GetInstance().CreateFirstDevice(info));

                // OPEN THE CAMERA FOR ACCESSING THE PARAMETERS.
                camera->Open();

                if (camera->IsOpen()) {
                    // GET THE MAKE, MODEL, AND SERIAL NUMBER STRINGS
                    makeString = QString(camera->DeviceVendorName.GetValue());
                    modelString = QString(camera->DeviceModelName.GetValue());
                    serialString = QString(camera->DeviceFirmwareVersion.GetValue());

                    if (modelString.contains(QString("acA1920"))) {
                        // TUNE THE RED, GREEN, AND BLUE GAINS FOR MAGENTA PROJECTION
                        camera->Gain.SetValue(1.0);

                        if (modelString.endsWith(QString("c"))) {
                            // SET THE WIDTH AND LEFT EDGE OF ROI
                            if (IsWritable(camera->Width)) {
                                camera->Width.SetValue(1088);
                            }

                            if (IsWritable(camera->Height)) {
                                camera->Height.SetValue(960);
                            }

                            if (IsWritable(camera->CenterX)) {
                                camera->CenterX.SetValue(false);
                                camera->OffsetX.SetValue(448);
                            }

                            if (IsWritable(camera->CenterY)) {
                                camera->CenterY.SetValue(true);
                            }

                            // MAKE SURE WE HAVE THE CURRENT ROI SIZE IN MEMORY
                            numCols = static_cast<int>(camera->Width.GetValue());
                            numRows = static_cast<int>(camera->Height.GetValue());

                            camera->LUTEnable.SetValue(false);
                            camera->LightSourcePreset.SetValue(LightSourcePreset_Off);
                            camera->ColorAdjustmentSelector.SetValue(ColorAdjustmentSelector_Green);
                            camera->ColorAdjustmentHue.SetValue(0.0);
                            camera->ColorAdjustmentSaturation.SetValue(1.0);

                            camera->BalanceRatioSelector.SetValue(BalanceRatioSelector_Red);
                            camera->BalanceRatio.SetValue(1.2700);
                            camera->BalanceRatioSelector.SetValue(BalanceRatioSelector_Green);
                            camera->BalanceRatio.SetValue(1.0000);
                            camera->BalanceRatioSelector.SetValue(BalanceRatioSelector_Blue);
                            camera->BalanceRatio.SetValue(1.8000);

                            if (IsWritable(camera->PixelFormat)) {
                                camera->PixelFormat.SetValue(PixelFormat_BayerBG10);
                            }
                        } else {
                            if (IsWritable(camera->PixelFormat)) {
                                camera->PixelFormat.SetValue(PixelFormat_Mono10);
                                bitsPerPixel = 10;
                            }

                            // ENABLE BINNING
                            if (IsWritable(camera->BinningHorizontalMode)) {
                                camera->BinningHorizontalMode.SetValue(BinningHorizontalMode_Average);
                                camera->BinningHorizontal.SetValue(2);
                            }
                            if (IsWritable(camera->BinningVerticalMode)) {
                                camera->BinningVerticalMode.SetValue(BinningVerticalMode_Average);
                                camera->BinningVertical.SetValue(2);
                            }

                            // SET THE WIDTH AND LEFT EDGE OF ROI
                            if (IsWritable(camera->Width)) {
                                camera->Width.SetValue(1088 / 2);
                            }

                            if (IsWritable(camera->Height)) {
                                camera->Height.SetValue(960 / 2);
                            }

                            if (IsWritable(camera->CenterX)) {
                                camera->CenterX.SetValue(true);
                            }

                            if (IsWritable(camera->CenterY)) {
                                camera->CenterY.SetValue(true);
                            }
                        }

                        // MAKE SURE WE HAVE THE CURRENT ROI SIZE IN MEMORY
                        numCols = camera->Width.GetValue();
                        numRows = camera->Height.GetValue();
                    } else {
                        // SET THE REGION OF INTEREST TO THE FULL SENSOR
                        if (IsWritable(camera->OffsetX)) {
                            camera->OffsetX.SetValue(camera->OffsetX.GetMin());
                        }

                        if (IsWritable(camera->OffsetY)) {
                            camera->OffsetY.SetValue(camera->OffsetY.GetMin());
                        }

                        if (IsWritable(camera->Width)) {
                            camera->Width.SetValue(camera->Width.GetMax());
                            numCols = camera->Width.GetValue();
                        }

                        if (IsWritable(camera->Height)) {
                            camera->Height.SetValue(camera->Height.GetMax());
                            numRows = camera->Height.GetValue();
                        }

                        if (IsWritable(camera->PixelFormat)) {
                            camera->PixelFormat.SetValue(PixelFormat_Mono10);
                            bitsPerPixel = 10;
                        }

                        if (IsWritable(camera->ReverseX)) {
                            camera->ReverseX.SetValue(true);
                            camera->ReverseY.SetValue(true);
                        }
                    }

                    if (IsWritable(camera->ExposureAuto)) {
                        camera->ExposureAuto.SetValue(ExposureAuto_Off);
                    }

                    // THE PARAMETER MAXNUMBUFFER CAN BE USED TO CONTROL THE COUNT OF BUFFERS
                    // ALLOCATED FOR GRABBING. THE DEFAULT VALUE OF THIS PARAMETER IS 10.
                    camera->MaxNumBuffer = numBtcs;

                    return (setSynchronization());
                }
                return (false);
            }
        }
    } catch (const GenericException &e) {
        errorString = QString("Pylon exception:").append(QString(e.GetDescription()));
        emit emitError(errorString);
    }

    // IF WE MAKE IT THIS FAR, BUT WE ARE NOT CONNECTED, THEN THE CAMERA STRING WASN'T FOUND
    return (false);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
bool LAUBaslerUSBCamera::reset()
{
    return (isConnected);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
bool LAUBaslerUSBCamera::setSynchronization()
{
    if (camera && camera->IsOpen()) {
//#define USEFREERUN
#ifdef USEFREERUN
        // ENABLE THE CAMERA TO GET IT TO START RECORDING FRAMES OF VIDEO
        camera->TriggerMode.SetValue(TriggerMode_Off);                     // default: off
#else
        // ENABLE THE CAMERA TO GET IT TO START RECORDING FRAMES OF VIDEO
        camera->TriggerSelector.SetValue(TriggerSelector_FrameStart);     // default: framestart
        camera->TriggerSource.SetValue(TriggerSource_Line1);              // default: line1
        camera->TriggerActivation.SetValue(TriggerActivation_RisingEdge); // default: rising edge
        camera->TriggerMode.SetValue(TriggerMode_On);                     // default: off
#ifdef USETIDLPPROJECTOR
        camera->TriggerDelay.SetValue(200);                              // delay one frame of video
#else
        camera->TriggerDelay.SetValue(0); //300                              // delay one frame of video
#endif
        // SET LINE 1 TO TRIGGER IN CHANNEL
        camera->LineSelector.SetValue(LineSelector_Line1);
        camera->LineMode.SetValue(LineMode_Input);
        camera->LineInverter.SetValue(false);

        // NOW SET LINE 2 OUTPUT TO LOW
        camera->UserOutputSelector.SetValue(UserOutputSelector_UserOutput1);
        camera->UserOutputValue.SetValue(false);

        // SET LINE 2 TO GENERAL PURPOSE OUTPUT
        camera->LineSelector.SetValue(LineSelector_Line2);
        camera->LineMode.SetValue(LineMode_Output);
        camera->LineSource.SetValue(LineSource_UserOutput1);
        camera->LineInverter.SetValue(true);

        // SET LINE 3 TO GENERAL PURPOSE INPUT
        camera->LineSelector.SetValue(LineSelector_Line3);
        camera->LineMode.SetValue(LineMode_Input);
        camera->LineInverter.SetValue(false);

        // SET LINE 4 TO FRAME TRIGGER READY
        camera->LineSelector.SetValue(LineSelector_Line4);
        camera->LineMode.SetValue(LineMode_Output);
        camera->LineSource.SetValue(LineSource_FrameTriggerWait);
        camera->LineInverter.SetValue(true);
#endif
        return (true);
    }
    return (false);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
bool LAUBaslerUSBCamera::disconnectFromHost()
{
    if (camera && camera->IsOpen()) {
        camera->Close();
        if (camera->IsOpen() == false) {
            return (true);
        }
    }
    return (true);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::onUpdateExposure(int microseconds)
{
    // SET THE CAMERA'S EXPOSURE
    if (camera && camera->IsOpen()) {
        camera->ExposureTime.SetValue(microseconds);
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::setCenterROI(double areaFraction)
{
    if (!camera || !camera->IsOpen()) {
        return;
    }
    if (areaFraction <= 0.0) {
        areaFraction = 1.0;
    } else if (areaFraction > 1.0) {
        areaFraction = 1.0;
    }
    // PER-DIMENSION FRACTION = sqrt(AREA FRACTION). e.g. 1/16 area -> 1/4 width & height.
    double linFrac = qSqrt(areaFraction);

    try {
        // PLACE THE WINDOW OURSELVES, NOT AUTO-CENTERED
        if (IsWritable(camera->CenterX)) {
            camera->CenterX.SetValue(false);
        }
        if (IsWritable(camera->CenterY)) {
            camera->CenterY.SetValue(false);
        }
        // MOVE OFFSETS TO MIN SO WIDTH/HEIGHT CAN REACH THE FULL SENSOR
        if (IsWritable(camera->OffsetX)) {
            camera->OffsetX.SetValue(camera->OffsetX.GetMin());
        }
        if (IsWritable(camera->OffsetY)) {
            camera->OffsetY.SetValue(camera->OffsetY.GetMin());
        }

        int64_t maxW = camera->Width.GetMax();
        int64_t maxH = camera->Height.GetMax();
        int64_t incW = qMax((int64_t)1, (int64_t)camera->Width.GetInc());
        int64_t incH = qMax((int64_t)1, (int64_t)camera->Height.GetInc());

        int64_t newW = (int64_t)(maxW * linFrac) / incW * incW;
        int64_t newH = (int64_t)(maxH * linFrac) / incH * incH;
        newW = qBound((int64_t)camera->Width.GetMin(),  newW, maxW);
        newH = qBound((int64_t)camera->Height.GetMin(), newH, maxH);

        if (IsWritable(camera->Width)) {
            camera->Width.SetValue(newW);
        }
        if (IsWritable(camera->Height)) {
            camera->Height.SetValue(newH);
        }

        // CENTRE THE WINDOW ON THE SENSOR
        int64_t incOX = qMax((int64_t)1, (int64_t)camera->OffsetX.GetInc());
        int64_t incOY = qMax((int64_t)1, (int64_t)camera->OffsetY.GetInc());
        int64_t offX = ((maxW - newW) / 2) / incOX * incOX;
        int64_t offY = ((maxH - newH) / 2) / incOY * incOY;
        offX = qBound((int64_t)camera->OffsetX.GetMin(), offX, (int64_t)camera->OffsetX.GetMax());
        offY = qBound((int64_t)camera->OffsetY.GetMin(), offY, (int64_t)camera->OffsetY.GetMax());
        if (IsWritable(camera->OffsetX)) {
            camera->OffsetX.SetValue(offX);
        }
        if (IsWritable(camera->OffsetY)) {
            camera->OffsetY.SetValue(offY);
        }

        numCols = (unsigned int)camera->Width.GetValue();
        numRows = (unsigned int)camera->Height.GetValue();
        emit emitROIChanged(numCols, numRows);
    } catch (const GenericException &e) {
        errorString = QString("ROI set failed: ").append(QString(e.GetDescription()));
        emit emitError(errorString);
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::setTriggerDelayMicroseconds(int microseconds)
{
    if (!camera || !camera->IsOpen()) {
        return;
    }
    try {
        if (IsWritable(camera->TriggerDelay)) {
            double v  = (double)microseconds;
            double lo = camera->TriggerDelay.GetMin();
            double hi = camera->TriggerDelay.GetMax();
            if (v < lo) {
                v = lo;
            } else if (v > hi) {
                v = hi;
            }
            camera->TriggerDelay.SetValue(v);
        }
    } catch (const GenericException &e) {
        errorString = QString("Trigger delay set failed: ").append(QString(e.GetDescription()));
        emit emitError(errorString);
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::updateBuffer(LAUMemoryObject object)
{
    if (object.isValid()) {
        // SET STARTING EXPOSURE TIME IN MICROSECONDS
        int microseconds = 200;

        // RESET ERROR CODE
        object.setConstElapsed(0);

        // CALCULATE HOW MANY OF FRAMES WE NEED TO GRAB
        unsigned int numFrmsToGrab = object.frames() + NUMBASLERUSBHEADERFRAMES;

        // THE PARAMETER MAXNUMBUFFER CAN BE USED TO CONTROL THE COUNT OF BUFFERS
        // ALLOCATED FOR GRABBING. THE DEFAULT VALUE OF THIS PARAMETER IS 10.
        camera->MaxNumBuffer = numBtcs + NUMBASLERUSBHEADERFRAMES;

        // KEEP TRACK OF HOW MANY FRAMES WE HAVE SO FAR COLLECTED
        unsigned int counter = 0;

        try {
            // HERE WE ARE GOING TO GRAB VIDEO IN BATCHES OF FRAMES
            while (counter < numFrmsToGrab && haltVideoFlag == false) {
                // SET THE EXPOSURE FOR THIS BATCH OF FRAMES AND
                // INCREMENT THE EXPOSURE FOR NEXT TIME AROUND
                if (enableMeasureSensorResponseFlag) {
                    camera->ExposureTime.SetValue(microseconds);
                    microseconds += 100;
                }

                // START THE GRABBING OF NUMBTCS IMAGES.
                camera->StartGrabbing(numBtcs + NUMBASLERUSBHEADERFRAMES);

                // NOW SET LINE 2 OUTPUT TO HIGH TO START PROJECTOR
                if (enableHDMITriggeringFlag == false) {
                    camera->UserOutputValue.SetValue(true);
                }

                // TELL THE PROJECTOR TO PROJECT THE NEXT BATCH FRAMES
                emit emitTriggerVideo((int)counter, (int)numBtcs + NUMBASLERUSBHEADERFRAMES);

                // GIVE THE CAMERA API TIME TO PREPARE FRAME GRABBING
                //QThread::msleep(100);

                // THIS SMART POINTER WILL RECEIVE THE GRAB RESULT DATA.
                CGrabResultPtr ptrGrabResult;

                // CAMERA.STOPGRABBING() IS CALLED AUTOMATICALLY BY THE RETRIEVERESULT() METHOD
                // WHEN C_COUNTOFIMAGESTOGRAB IMAGES HAVE BEEN RETRIEVED.
                while (camera->IsGrabbing() && counter < numFrmsToGrab) {
                    // WAIT FOR AN IMAGE AND THEN RETRIEVE IT. A TIMEOUT OF 5000 MS IS USED.
                    camera->RetrieveResult(5000, ptrGrabResult, TimeoutHandling_Return);

                    // IMAGE GRABBED SUCCESSFULLY?
                    if (ptrGrabResult->GrabSucceeded()) {
                        // DUMP ANY HEADER FRAMES INTO THE FIRST AVAILABLE BUFFER TO BE OVER WRITTEN SHORTLY
                        if (counter < NUMBASLERUSBHEADERFRAMES){
                            memcpy(object.constFrame(0), ptrGrabResult->GetBuffer(), object.block());
                        } else {
                            // SIMULTANEOUSLY DEBAYER THE INCOMING BUFFER AND COPY TO THE OBJECT BUFFER
                            //debayer(object.constFrame(counter - NUMBASLERUSBHEADERFRAMES), (unsigned char *)ptrGrabResult->GetBuffer(), numRows, numCols, numCols * sizeof(unsigned short));
                            memcpy(object.constFrame(counter - NUMBASLERUSBHEADERFRAMES), ptrGrabResult->GetBuffer(), object.block());

                            // CALCULATE THE AVERAGE PIXEL VALUE FOR CALIBRATION
                            if (enableCalibrationFlag) {
                                __m128i accumPixel = _mm_set1_epi32(0);
                                unsigned char *buffer = (unsigned char *)(object.constFrame(counter - NUMBASLERUSBHEADERFRAMES));
                                for (unsigned int n = 0; n < object.block(); n += 4) {
                                    accumPixel = _mm_add_epi64(accumPixel, _mm_cvtepu16_epi64(_mm_loadu_si128((__m128i *)&buffer[n])));
                                }

                                // EXTRACT THE SUM OF ALL PIXELS AND CALCULATE THE MEAN VALUE
                                int pixel = (_mm_extract_epi64(accumPixel, 0) + _mm_extract_epi64(accumPixel, 1)) / (numRows * numCols);
    #ifdef RECORDSTATISTICS
                                if (stream.status() == QTextStream::Ok) {
                                    stream << pixel << ",";
                                }
    #endif
                                emit emitMeanPixel(counter - NUMBASLERUSBHEADERFRAMES, pixel);

                                // STORE THE MEAN VALUE INTO THE TOP-LEFT PIXEL OF THE CURRENT FRAME
                                ((unsigned short *)buffer)[0] = (unsigned short)pixel;
                            }
                        }
                    } else {
                        errorString = QString("Error grabbing frame:").append(QString(ptrGrabResult->GetErrorCode())).append(QString(ptrGrabResult->GetErrorDescription()));
                        object.setConstElapsed((unsigned int)(-1));
                        counter = object.frames() + NUMBASLERUSBHEADERFRAMES;
                        break;
                    }

                    // INCREMENT THE COUNTER FOR THE NEXT FRAME
                    counter++;
                }
                QThread::msleep(100);

                // NOW SET LINE 2 OUTPUT TO HIGH TO START PROJECTOR
                if (enableHDMITriggeringFlag == false) {
                    camera->UserOutputValue.SetValue(false);
                }
            }
        } catch (const GenericException &e) {
            errorString = QString("Pylon exception:").append(QString(e.GetDescription()));
            emit emitError(errorString);
        }

#ifdef RECORDSTATISTICS
        if (stream.status() == QTextStream::Ok) {
            stream << "\r\n";
        }
#endif
        // NOW SET LINE 2 OUTPUT TO LOW TO RESET PROJECTOR
        //if (enableHDMITriggeringFlag == false) {
        //    camera->UserOutputValue.SetValue(false);
        //}

        // NOW WE NEED TO RESET THE PROJECTOR
        emit emitDisplayFrame(-1);

        //qDebug() << "elapsed time:" << object.elapsed();

        // GIVE THE PROJECTOR TIME TO CLEAR ITSELF
        //QThread::msleep(1000);
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::unpack10Bits(unsigned char *toBuffer, unsigned char *fmBuffer)
{
    int shifts[4] = {6, 4, 2, 0};
    for (int index = 0; index < numRows * numCols; index++) {
        unsigned short pixel = ((*(unsigned short *)(fmBuffer + (index * 10 / 8))) >> shifts[index % 4]) & 0x03ff;
        ((unsigned short *)toBuffer)[index] = pixel;
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUBaslerUSBCamera::debayer(unsigned char *otBuffer, unsigned char *inBuffer, unsigned int rows, unsigned int cols, unsigned int step)
{
    memcpy(otBuffer, inBuffer, rows * step);
    return;

    __m128i pixelVec;
    __m128i shuffleA = _mm_set_epi8(15, 14, 15, 14, 11, 10, 11, 10, 7, 6, 7, 6, 3, 2, 3, 2);
    __m128i shuffleB = _mm_set_epi8(13, 12, 13, 12, 9, 8, 9, 8, 5, 4, 5, 4, 1, 0, 1, 0);

    for (unsigned int row = 0; row < rows; row++) {
        if (row % 2 == 0) {
            for (unsigned int col = 0; col < cols; col += 8) {
                pixelVec = _mm_load_si128((const __m128i *)(inBuffer + col * sizeof(unsigned short)));
                pixelVec = _mm_shuffle_epi8(pixelVec, shuffleA);
                _mm_store_si128((__m128i *)(otBuffer + col * sizeof(unsigned short)), pixelVec);
            }
        } else {
            for (unsigned int col = 0; col < cols; col += 8) {
                pixelVec = _mm_load_si128((const __m128i *)(inBuffer + col * sizeof(unsigned short)));
                pixelVec = _mm_shuffle_epi8(pixelVec, shuffleB);
                _mm_store_si128((__m128i *)(otBuffer + col * sizeof(unsigned short)), pixelVec);
            }
        }
        // INCREMENT THE BUFFER POINTERS TO THE NEXT ROW OF PIXELS
        inBuffer += step;
        otBuffer += step;
    }
}

