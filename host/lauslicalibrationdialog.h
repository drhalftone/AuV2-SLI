/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 *                                                                               *
 * LAUSLICalibrationDialog -- minimal host app that coordinates a Basler USB     *
 * camera with the Alchitry Au V2 SLI FPGA to build and upload an 8-bit          *
 * intensity-linearisation (CORRECTION) table.                                   *
 *                                                                               *
 *   ramp window (HDMI) --> projector --> camera (mean pixel per gray level)     *
 *        --> LAUToneCorrectionWidget (inverse-response curve)                    *
 *        --> LAUAuBoard.uploadCorrectionTable() --> FPGA corr[] RAM (TARGET 2)   *
 *                                                                               *
 * The FPGA then renders the linearised sinusoid on the fly: out = corr[cos].    *
 *                                                                               *
 *********************************************************************************/

#ifndef LAUSLICALIBRATIONDIALOG_H
#define LAUSLICALIBRATIONDIALOG_H

#include <QDialog>
#include <QWidget>
#include <QThread>
#include <QLabel>
#include <QTimer>
#include <QSpinBox>
#include <QComboBox>
#include <QCheckBox>
#include <QPainter>
#include <QGroupBox>
#include <QPushButton>
#include <QProgressBar>
#include <QPaintEvent>
#include <QTabWidget>
#include <QDoubleSpinBox>
#include <QVector>

#include "laumemoryobject.h"
#include "lauauboard.h"
#include "lautonecorrectionwidget.h"
#include "lauxyplotwidget.h"
#ifdef USEBASLERUSBCAMERA
#include "laubaslerusbcamera.h"
#endif

/****************************************************************************/
/* Frameless full-screen window that paints a single flat gray level on the */
/* projector. Drive it with onSetLevel() between captures.                  */
/****************************************************************************/
class LAURampWindow : public QWidget
{
    Q_OBJECT

public:
    explicit LAURampWindow(QWidget *parent = nullptr) : QWidget(parent, Qt::Window | Qt::FramelessWindowHint), level(0)
    {
        flashTimer = new QTimer(this);
        connect(flashTimer, &QTimer::timeout, this, [this]() {
            level = level ? 0 : 255;
            update();
        });
    }

public slots:
    void onSetLevel(int value)
    {
        flashTimer->stop();
        level = qBound(0, value, 255);
        update();
    }

    // Flash the full field white/black (the "WBWB" sequence). In HDMI pass-through this
    // also generates a per-frame top-left-pixel camera trigger. periodMs = half-cycle.
    void onSetFlashing(bool on, int periodMs = 16)
    {
        if (on) {
            level = 255;
            update();
            flashTimer->start(qMax(1, periodMs));
        } else {
            flashTimer->stop();
        }
    }

protected:
    void paintEvent(QPaintEvent *) override
    {
        QPainter painter(this);
        painter.fillRect(rect(), QColor(level, level, level));
    }

private:
    int level;
    QTimer *flashTimer;
};

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
class LAUSLICalibrationDialog : public QDialog
{
    Q_OBJECT

public:
    explicit LAUSLICalibrationDialog(QWidget *parent = nullptr);
    ~LAUSLICalibrationDialog();

signals:
    // queued request to the camera worker: grab one measurement into buffer.
    void emitGrab(LAUMemoryObject buffer);

private slots:
    void onConnectBoard();
    void onResetCorrection();
    void onApplySLIControl();
    void onConnectCamera();
    void onShowRampWindow();
    void onRunSweep();
    void onStepSweep();             // show level, schedule a grab
    void onMeanPixel(unsigned int frame, unsigned int mean);
    void onUploadCorrection();
    void onVerifyCorrection();       // read the correction table back and compare
    void onSaveCurve();
    void onLoadCurve();

    // trigger-delay sweep experiment (projector temporal light profile)
    void onRunTriggerSweep();
    void onStopTriggerSweep();
    void onROIChanged(unsigned int width, unsigned int height);
    void onTriggerGrabComplete(LAUMemoryObject buffer);
    void onExportTriggerData();

private:
    void refreshBoardStatus();
    void setBusy(bool busy);
    void stepTriggerDelay();        // set delay, grab a batch at the current delay

    // hardware interfaces
    LAUAuBoard *board;
#ifdef USEBASLERUSBCAMERA
    LAUBaslerUSBCamera *camera;
#endif
    QThread cameraThread;

    // ui
    LAUToneCorrectionWidget *toneWidget;
    LAURampWindow *rampWindow;
    QComboBox *portCombo;
    QComboBox *screenCombo;
    QLabel *boardStatusLabel;
    QLabel *cameraStatusLabel;
    QSpinBox *exposureSpin;
    QSpinBox *latencySpin;
    QCheckBox *hdmiTriggerCheck;
    QCheckBox *orientCheck;
    QCheckBox *rCheck, *gCheck, *bCheck;
    QCheckBox *usbOverrideCheck;     // 0x13 bit7: USB drives R/G/B/orient instead of the PCB switches
    QProgressBar *progress;
    QPushButton *runButton, *uploadButton;

    // sweep state (linearization)
    bool sweeping;
    int  sweepLevel;
    LAUMemoryObject grabBuffer;
    QByteArray lastCorrTable;        // last 256-byte correction sent (for read-back verify)

    // trigger-delay sweep UI
    LAUXYPlotWidget *delayPlot;
    QSpinBox *roiDivisorSpin;       // ROI = central 1/N of the FOV area
    QSpinBox *trigExposureSpin;     // short exposure (us)
    QSpinBox *delayStartSpin, *delayStopSpin, *delayStepSpin;   // trigger-delay sweep (us)
    QSpinBox *framesAvgSpin;        // frames averaged per delay
    QSpinBox *flashPeriodSpin;      // projector flash half-cycle (ms)
    QPushButton *trigRunButton, *trigStopButton, *trigExportButton;
    QLabel *trigStatusLabel;

    // trigger-delay sweep state
    bool triggerSweeping;
    int  curDelay, trigDelayStop, trigDelayStep;
    QVector<double> curDelayMeans;  // per-frame means accumulated at the current delay
    LAUMemoryObject triggerBuffer;  // N-frame grab buffer sized to the ROI
};

#endif // LAUSLICALIBRATIONDIALOG_H
