/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 *                                                                               *
 * AuV2-SLI host -- coordinate a Basler USB camera with the Alchitry Au V2 SLI   *
 * FPGA to build and upload an 8-bit intensity-linearisation (correction) table. *
 *                                                                               *
 *********************************************************************************/

#include <QApplication>

#include "laumemoryobject.h"
#include "lauslicalibrationdialog.h"

using namespace LAU3DVideoParameters;

int main(int argc, char *argv[])
{
    QApplication a(argc, argv);
    a.setOrganizationName(QString("Lau Consulting Inc"));
    a.setOrganizationDomain(QString("drhalftone.com"));
    a.setApplicationName(QString("AuV2SLIHost"));

    // REQUIRED FOR QUEUED SIGNALS THAT CARRY LAUMemoryObject ACROSS THREADS.
    qRegisterMetaType<LAUMemoryObject>("LAUMemoryObject");

    LAUSLICalibrationDialog dialog;
    dialog.show();

    return (a.exec());
}
