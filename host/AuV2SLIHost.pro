# AuV2-SLI host -- Basler camera <-> Au V2 FPGA linearisation tool.
# Builds a 256-byte 8-bit correction table and uploads it over USB (TARGET 0x02).
#
# Prerequisites (adjust the paths below to your install):
#   - Qt 5.15+/6 with the `serialport` module
#   - libtiff (used by laumemoryobject)
#   - Basler pylon SDK (USEBASLERUSBCAMERA) -- AUTO-DETECTED below; the app builds
#     without it (only the live camera sweep is disabled).

QT       += core gui widgets serialport

TEMPLATE = app
TARGET   = AuV2SLIHost
CONFIG  += c++17
DEFINES  += QT_DEPRECATED_WARNINGS

# ---- Basler pylon camera support: auto-detected & optional ------------------
# qmake probes the standard pylon install locations below. If a pylon Development
# tree is found, camera support is compiled in; otherwise it is skipped cleanly
# (the app still builds -- only the live calibration sweep is unavailable).
#   Force OFF         : qmake CONFIG+=nobasler
#   Non-standard path : qmake PYLON_ROOT="C:/path/to/pylon X/Development"
win32 {
    isEmpty(PYLON_ROOT):exists("C:/Program Files/Basler/pylon 8/Development/include/pylon/PylonIncludes.h"): PYLON_ROOT = "C:/Program Files/Basler/pylon 8/Development"
    isEmpty(PYLON_ROOT):exists("C:/Program Files/Basler/pylon 7/Development/include/pylon/PylonIncludes.h"): PYLON_ROOT = "C:/Program Files/Basler/pylon 7/Development"
}
unix:!macx: isEmpty(PYLON_ROOT):exists(/opt/pylon/include/pylon/PylonIncludes.h): PYLON_ROOT = /opt/pylon
unix:macx:  isEmpty(PYLON_ROOT):exists(/Library/Frameworks/pylon.framework): PYLON_ROOT = /Library/Frameworks/pylon.framework

nobasler: PYLON_ROOT =                          # honour an explicit force-off
!isEmpty(PYLON_ROOT): CONFIG += basler

basler:  message("AuV2SLIHost: camera ENABLED  (pylon: $$PYLON_ROOT)")
!basler: message("AuV2SLIHost: camera DISABLED (no pylon SDK found; set PYLON_ROOT to enable)")

HEADERS += laumemoryobject.h \
           lautonecorrectionwidget.h \
           lauauboard.h \
           lauxyplotwidget.h \
           lauslicalibrationdialog.h

SOURCES += laumemoryobject.cpp \
           lautonecorrectionwidget.cpp \
           lauauboard.cpp \
           lauxyplotwidget.cpp \
           lauslicalibrationdialog.cpp \
           main.cpp

basler {
    DEFINES += USEBASLERUSBCAMERA
    HEADERS += laubaslerusbcamera.h
    SOURCES += laubaslerusbcamera.cpp
}

# ---------------------------------------------------------------------------
win32 {
    # libtiff
    INCLUDEPATH += $$quote(C:/usr/Tiff/include)
    DEPENDPATH  += $$quote(C:/usr/Tiff/include)
    LIBS        += -L$$quote(C:/usr/Tiff/lib) -ltiff

    basler {
        # pylon root auto-detected above (PYLON_ROOT). -lPylonBase_v8_0 suits pylon 8;
        # for pylon 7 adjust the lib name to your installed version.
        INCLUDEPATH += $$quote($$PYLON_ROOT/include)
        DEPENDPATH  += $$quote($$PYLON_ROOT/include)
        LIBS        += -L$$quote($$PYLON_ROOT/lib/x64) -lPylonBase_v8_0
    }
}

unix:!macx {
    QMAKE_CXXFLAGS += -msse2 -msse3 -mssse3 -msse4.1
    INCLUDEPATH    += /usr/include/eigen3
    LIBS           += -ltiff

    basler {
        INCLUDEPATH += /opt/pylon/include /opt/pylon/include/GenApi
        LIBS        += -L/opt/pylon/lib -lpylonbase -lpylonutility \
                       -lGenApi_gcc_v3_1_Basler_pylon -lGCBase_gcc_v3_1_Basler_pylon
    }
}

unix:macx {
    CONFIG         += sdk_no_version_check
    QMAKE_CXXFLAGS += -msse2 -msse3 -mssse3 -msse4.1
    INCLUDEPATH    += /usr/local/include/Tiff /usr/local/include/eigen3
    LIBS           += /usr/local/lib/libtiff.dylib

    basler {
        INCLUDEPATH += /Library/Frameworks/pylon.framework/Versions/A/Headers \
                       /Library/Frameworks/pylon.framework/Versions/A/Headers/GenICam
        LIBS        += /Library/Frameworks/pylon.framework/Versions/A/pylon
    }
}
