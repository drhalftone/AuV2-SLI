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

#include "laumemoryobject.h"
#include "lauauboard.h"
#include "lautonecorrectionwidget.h"
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
    explicit LAURampWindow(QWidget *parent = nullptr) : QWidget(parent, Qt::Window | Qt::FramelessWindowHint), level(0) { ; }

public slots:
    void onSetLevel(int value)
    {
        level = qBound(0, value, 255);
        update();
    }

protected:
    void paintEvent(QPaintEvent *) override
    {
        QPainter painter(this);
        painter.fillRect(rect(), QColor(level, level, level));
    }

private:
    int level;
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
    void onSaveCurve();
    void onLoadCurve();

private:
    void refreshBoardStatus();
    void setBusy(bool busy);

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
    QProgressBar *progress;
    QPushButton *runButton, *uploadButton;

    // sweep state
    bool sweeping;
    int  sweepLevel;
    LAUMemoryObject grabBuffer;
};

#endif // LAUSLICALIBRATIONDIALOG_H
