# AuV2-SLI host -- Basler camera <-> Au V2 FPGA linearisation tool.
# Builds a 256-byte 8-bit correction table and uploads it over USB (TARGET 0x02).
#
# Prerequisites (adjust the paths below to your install):
#   - Qt 5.15+/6 with the `serialport` module
#   - libtiff (used by laumemoryobject)
#   - Basler pylon SDK (USEBASLERUSBCAMERA) -- build with `CONFIG-=basler` to omit

QT       += core gui widgets serialport

TEMPLATE = app
TARGET   = AuV2SLIHost
CONFIG  += c++17
DEFINES  += QT_DEPRECATED_WARNINGS

# camera support is on by default; disable with `qmake CONFIG+=nobasler`
CONFIG  += basler
nobasler: CONFIG -= basler

HEADERS += laumemoryobject.h \
           lautonecorrectionwidget.h \
           lauauboard.h \
           lauslicalibrationdialog.h

SOURCES += laumemoryobject.cpp \
           lautonecorrectionwidget.cpp \
           lauauboard.cpp \
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
        # Basler pylon 8 (adjust to "pylon 7" / lib version as installed)
        INCLUDEPATH += $$quote(C:/Program Files/Basler/pylon 8/Development/include)
        DEPENDPATH  += $$quote(C:/Program Files/Basler/pylon 8/Development/include)
        LIBS        += -L$$quote(C:/Program Files/Basler/pylon 8/Development/lib/x64) -lPylonBase_v8_0
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
