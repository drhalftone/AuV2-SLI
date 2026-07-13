/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau -- LAUSLICalibrationDialog impl.        *
 *                                                                               *
 *********************************************************************************/

#include "lauslicalibrationdialog.h"

#include <QScreen>
#include <QGuiApplication>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGridLayout>
#include <QMessageBox>
#include <QFileDialog>
#include <QSettings>
#include <QStandardPaths>

/****************************************************************************/
LAUSLICalibrationDialog::LAUSLICalibrationDialog(QWidget *parent)
    : QDialog(parent), board(nullptr),
#ifdef USEBASLERUSBCAMERA
      camera(nullptr),
#endif
      sweeping(false), sweepLevel(0),
      triggerSweeping(false), curDelay(0), trigDelayStop(0), trigDelayStep(0)
{
    setWindowTitle(QString("AuV2-SLI Linearisation + Diagnostics"));

    // ---- FPGA board group -----------------------------------------------------
    QGroupBox *boardBox = new QGroupBox(QString("Au V2 board (USB)"));
    {
        portCombo = new QComboBox();
        portCombo->addItems(LAUAuBoard::availablePorts());
        QPushButton *connectButton = new QPushButton(QString("Connect"));
        connect(connectButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onConnectBoard);

        boardStatusLabel = new QLabel(QString("not connected"));
        boardStatusLabel->setWordWrap(true);

        orientCheck = new QCheckBox(QString("horizontal stripes (rows)"));
        rCheck = new QCheckBox(QString("R"));
        gCheck = new QCheckBox(QString("G"));
        bCheck = new QCheckBox(QString("B"));
        rCheck->setChecked(true);
        gCheck->setChecked(true);
        bCheck->setChecked(true);
        usbOverrideCheck = new QCheckBox(QString("USB override"));
        usbOverrideCheck->setChecked(true);
        usbOverrideCheck->setToolTip(QString("Drive R/G/B/orientation from USB (reg 0x13 bit7) instead of the PCB switch pins"));
        QPushButton *applyButton = new QPushButton(QString("Apply SLI control"));
        connect(applyButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onApplySLIControl);
        QPushButton *resetButton = new QPushButton(QString("Reset correction (identity)"));
        connect(resetButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onResetCorrection);
        QPushButton *verifyButton = new QPushButton(QString("Verify correction (read back)"));
        connect(verifyButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onVerifyCorrection);

        QGridLayout *grid = new QGridLayout();
        grid->addWidget(new QLabel(QString("Port:")), 0, 0);
        grid->addWidget(portCombo, 0, 1);
        grid->addWidget(connectButton, 0, 2);
        grid->addWidget(boardStatusLabel, 1, 0, 1, 3);
        QHBoxLayout *ctrlRow = new QHBoxLayout();
        ctrlRow->addWidget(usbOverrideCheck);
        ctrlRow->addWidget(orientCheck);
        ctrlRow->addWidget(rCheck);
        ctrlRow->addWidget(gCheck);
        ctrlRow->addWidget(bCheck);
        ctrlRow->addWidget(applyButton);
        grid->addLayout(ctrlRow, 2, 0, 1, 3);
        QHBoxLayout *corrRow = new QHBoxLayout();
        corrRow->addWidget(resetButton);
        corrRow->addWidget(verifyButton);
        grid->addLayout(corrRow, 3, 0, 1, 3);
        boardBox->setLayout(grid);
    }

    // ---- camera group ---------------------------------------------------------
    QGroupBox *camBox = new QGroupBox(QString("Basler USB camera"));
    {
        QPushButton *connectButton = new QPushButton(QString("Connect camera"));
        connect(connectButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onConnectCamera);
        cameraStatusLabel = new QLabel(QString("not connected"));
        cameraStatusLabel->setWordWrap(true);

        exposureSpin = new QSpinBox();
        exposureSpin->setRange(50, 200000);
        exposureSpin->setValue(8200);
        exposureSpin->setSuffix(QString(" us"));
        connect(exposureSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, [this](int us) {
#ifdef USEBASLERUSBCAMERA
            if (camera) {
                QMetaObject::invokeMethod(camera, "onUpdateExposure", Qt::QueuedConnection, Q_ARG(int, us));
            }
#else
            Q_UNUSED(us);
#endif
        });

        hdmiTriggerCheck = new QCheckBox(QString("HDMI/line trigger (camera triggered by projector VSYNC)"));
        hdmiTriggerCheck->setChecked(true);

        QFormLayout *form = new QFormLayout();
        form->addRow(connectButton, cameraStatusLabel);
        form->addRow(QString("Exposure:"), exposureSpin);
        form->addRow(hdmiTriggerCheck);
        camBox->setLayout(form);
    }

    // ---- projector group ------------------------------------------------------
    QGroupBox *projBox = new QGroupBox(QString("Projector ramp window"));
    {
        screenCombo = new QComboBox();
        const QList<QScreen *> screens = QGuiApplication::screens();
        for (int n = 0; n < screens.count(); n++) {
            QRect g = screens.at(n)->geometry();
            screenCombo->addItem(QString("%1 (%2x%3)").arg(screens.at(n)->name()).arg(g.width()).arg(g.height()));
        }
        QPushButton *showButton = new QPushButton(QString("Show ramp window"));
        connect(showButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onShowRampWindow);

        latencySpin = new QSpinBox();
        latencySpin->setRange(0, 2000);
        latencySpin->setValue(120);
        latencySpin->setSuffix(QString(" ms"));

        QFormLayout *form = new QFormLayout();
        form->addRow(QString("Screen:"), screenCombo);
        form->addRow(showButton);
        form->addRow(QString("Settle / display latency:"), latencySpin);
        projBox->setLayout(form);
    }

    // ---- TAB 1: linearisation -------------------------------------------------
    toneWidget = new LAUToneCorrectionWidget(256);
    progress = new QProgressBar();
    progress->setRange(0, 255);
    runButton = new QPushButton(QString("Run linearisation sweep"));
    connect(runButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onRunSweep);
    uploadButton = new QPushButton(QString("Upload correction to FPGA"));
    uploadButton->setEnabled(false);
    connect(uploadButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onUploadCorrection);
    QPushButton *saveButton = new QPushButton(QString("Save curve (.tcc)"));
    connect(saveButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onSaveCurve);
    QPushButton *loadButton = new QPushButton(QString("Load + upload (.tcc)"));
    connect(loadButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onLoadCurve);

    QHBoxLayout *buttonRow = new QHBoxLayout();
    buttonRow->addWidget(runButton);
    buttonRow->addWidget(uploadButton);
    buttonRow->addWidget(saveButton);
    buttonRow->addWidget(loadButton);

    QWidget *linTab = new QWidget();
    QVBoxLayout *linLayout = new QVBoxLayout();
    linLayout->addWidget(toneWidget, 1);
    linLayout->addWidget(progress);
    linLayout->addLayout(buttonRow);
    linTab->setLayout(linLayout);

    // ---- TAB 2: trigger-delay sweep (projector temporal light profile) --------
    QWidget *trigTab = new QWidget();
    {
        roiDivisorSpin = new QSpinBox();
        roiDivisorSpin->setRange(1, 1024);
        roiDivisorSpin->setValue(16);
        roiDivisorSpin->setPrefix(QString("1/"));

        trigExposureSpin = new QSpinBox();
        trigExposureSpin->setRange(1, 100000);
        trigExposureSpin->setValue(100);
        trigExposureSpin->setSuffix(QString(" us"));

        delayStartSpin = new QSpinBox();
        delayStartSpin->setRange(0, 1000000);
        delayStartSpin->setValue(0);
        delayStartSpin->setSuffix(QString(" us"));
        delayStopSpin = new QSpinBox();
        delayStopSpin->setRange(0, 1000000);
        delayStopSpin->setValue(20000);
        delayStopSpin->setSuffix(QString(" us"));
        delayStepSpin = new QSpinBox();
        delayStepSpin->setRange(1, 100000);
        delayStepSpin->setValue(100);
        delayStepSpin->setSuffix(QString(" us"));

        framesAvgSpin = new QSpinBox();
        framesAvgSpin->setRange(1, 256);
        framesAvgSpin->setValue(8);

        flashPeriodSpin = new QSpinBox();
        flashPeriodSpin->setRange(1, 1000);
        flashPeriodSpin->setValue(16);
        flashPeriodSpin->setSuffix(QString(" ms"));

        delayPlot = new LAUXYPlotWidget();
        delayPlot->setLabels(QString("trigger delay (us)"), QString("mean pixel"));

        trigRunButton = new QPushButton(QString("Run trigger-delay sweep"));
        connect(trigRunButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onRunTriggerSweep);
        trigStopButton = new QPushButton(QString("Stop"));
        trigStopButton->setEnabled(false);
        connect(trigStopButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onStopTriggerSweep);
        trigExportButton = new QPushButton(QString("Export CSV"));
        connect(trigExportButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onExportTriggerData);

        trigStatusLabel = new QLabel(QString("idle"));
        trigStatusLabel->setWordWrap(true);

        QGridLayout *cfg = new QGridLayout();
        cfg->addWidget(new QLabel(QString("Center ROI (1/N of FOV area):")), 0, 0); cfg->addWidget(roiDivisorSpin,   0, 1);
        cfg->addWidget(new QLabel(QString("Exposure:")),                     0, 2); cfg->addWidget(trigExposureSpin, 0, 3);
        cfg->addWidget(new QLabel(QString("Delay start:")),                  1, 0); cfg->addWidget(delayStartSpin,   1, 1);
        cfg->addWidget(new QLabel(QString("Delay stop:")),                   1, 2); cfg->addWidget(delayStopSpin,    1, 3);
        cfg->addWidget(new QLabel(QString("Delay step:")),                   2, 0); cfg->addWidget(delayStepSpin,    2, 1);
        cfg->addWidget(new QLabel(QString("Frames / delay:")),               2, 2); cfg->addWidget(framesAvgSpin,    2, 3);
        cfg->addWidget(new QLabel(QString("Flash half-cycle:")),             3, 0); cfg->addWidget(flashPeriodSpin,  3, 1);

        QHBoxLayout *trigButtons = new QHBoxLayout();
        trigButtons->addWidget(trigRunButton);
        trigButtons->addWidget(trigStopButton);
        trigButtons->addWidget(trigExportButton);

        QVBoxLayout *trigLayout = new QVBoxLayout();
        trigLayout->addLayout(cfg);
        trigLayout->addWidget(delayPlot, 1);
        trigLayout->addWidget(trigStatusLabel);
        trigLayout->addLayout(trigButtons);
        trigTab->setLayout(trigLayout);
    }

    QTabWidget *tabs = new QTabWidget();
    tabs->addTab(linTab, QString("Linearisation"));
    tabs->addTab(trigTab, QString("Trigger-delay sweep"));

    // ---- assemble (shared hardware panel + experiment tabs) -------------------
    QVBoxLayout *layout = new QVBoxLayout();
    layout->addWidget(boardBox);
    layout->addWidget(camBox);
    layout->addWidget(projBox);
    layout->addWidget(tabs, 1);
    setLayout(layout);

    rampWindow = new LAURampWindow();
    resize(640, 800);
}

/****************************************************************************/
LAUSLICalibrationDialog::~LAUSLICalibrationDialog()
{
#ifdef USEBASLERUSBCAMERA
    LAUBaslerUSBCamera::haltVideoFlag = true;
    if (cameraThread.isRunning()) {
        cameraThread.quit();
        cameraThread.wait(3000);
    }
    if (camera) {
        delete camera;
    }
#endif
    if (board) {
        delete board;
    }
    if (rampWindow) {
        delete rampWindow;
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::onConnectBoard()
{
    if (board) {
        delete board;
        board = nullptr;
    }
    QString port = portCombo->currentText();
    board = new LAUAuBoard(port);
    if (!board->isValid()) {
        boardStatusLabel->setText(QString("ERROR: %1").arg(board->error()));
        QMessageBox::warning(this, windowTitle(), board->error());
        return;
    }
    if (!board->verifyIdentity()) {
        boardStatusLabel->setText(QString("WARNING: %1").arg(board->error()));
    }
    refreshBoardStatus();
}

/****************************************************************************/
void LAUSLICalibrationDialog::refreshBoardStatus()
{
    if (!board || !board->isValid()) {
        boardStatusLabel->setText(QString("not connected"));
        return;
    }
    int id = board->readRegister(LAUAU_REG_ID);
    int ver = board->readRegister(LAUAU_REG_VERSION);
    int status = board->readRegister(LAUAU_REG_STATUS);
    int flags = board->readRegister(LAUAU_REG_FLAGS);
    int pins = board->readRegister(LAUAU_REG_PINS);
    auto nib = [](int n) {
        return QString("R%1G%2B%3/%4").arg((n >> 3) & 1).arg((n >> 2) & 1).arg((n >> 1) & 1).arg((n & 1) ? "horiz" : "vert");
    };
    QString pinsText = (pins < 0) ? QString("PINS=?")
                                  : QString("switches phys[%1] active[%2]").arg(nib(pins & 0x0F)).arg(nib((pins >> 4) & 0x0F));
    boardStatusLabel->setText(QString("%1  ID=0x%2 VER=0x%3 STATUS=0x%4 FLAGS=0x%5\n%6")
                                  .arg(board->portName())
                                  .arg(id < 0 ? 0 : id, 2, 16, QChar('0'))
                                  .arg(ver < 0 ? 0 : ver, 2, 16, QChar('0'))
                                  .arg(status < 0 ? 0 : status, 2, 16, QChar('0'))
                                  .arg(flags < 0 ? 0 : flags, 2, 16, QChar('0'))
                                  .arg(pinsText));
}

/****************************************************************************/
void LAUSLICalibrationDialog::onResetCorrection()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    if (board->uploadIdentityCorrection()) {
        lastCorrTable = LAUAuBoard::identityTable();
        QMessageBox::information(this, windowTitle(), QString("Correction reset to identity (no linearisation)."));
    } else {
        QMessageBox::warning(this, windowTitle(), board->error());
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::onApplySLIControl()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    if (!board->setSLIControl(usbOverrideCheck->isChecked(), rCheck->isChecked(), gCheck->isChecked(), bCheck->isChecked(), orientCheck->isChecked())) {
        QMessageBox::warning(this, windowTitle(), board->error());
    }
    refreshBoardStatus();
}

/****************************************************************************/
void LAUSLICalibrationDialog::onConnectCamera()
{
#ifdef USEBASLERUSBCAMERA
    if (camera) {
        cameraStatusLabel->setText(QString("already connected: %1").arg(camera->model()));
        return;
    }
    // ONE FRAME PER GRAB; THE SWEEP REQUESTS A GRAB PER GRAY LEVEL.
    camera = new LAUBaslerUSBCamera(1, 1);
    if (!camera->isValid()) {
        cameraStatusLabel->setText(QString("ERROR: %1").arg(camera->error()));
        QMessageBox::warning(this, windowTitle(), camera->error());
        delete camera;
        camera = nullptr;
        return;
    }

    LAUBaslerUSBCamera::haltVideoFlag = false;
    camera->enableCalibration(true);
    camera->enableHDMITriggering(hdmiTriggerCheck->isChecked());

    // SIZE THE PER-LEVEL GRAB BUFFER (SINGLE FRAME, 16-bit MONO).
    grabBuffer = LAUMemoryObject(camera->width(), camera->height(), 1, sizeof(unsigned short), 1);

    // MOVE THE CAMERA TO A WORKER THREAD AND WIRE QUEUED SIGNALS.
    // (the destructor stops the thread then deletes the camera explicitly, so we do
    //  NOT also connect finished->deleteLater -- that would double-free.)
    camera->moveToThread(&cameraThread);
    connect(this, &LAUSLICalibrationDialog::emitGrab, camera, &LAUBaslerUSBCamera::onUpdateBuffer, Qt::QueuedConnection);
    connect(camera, &LAUBaslerUSBCamera::emitMeanPixel, this, &LAUSLICalibrationDialog::onMeanPixel, Qt::QueuedConnection);
    connect(camera, &LAUBaslerUSBCamera::emitBuffer, this, &LAUSLICalibrationDialog::onTriggerGrabComplete, Qt::QueuedConnection);
    connect(camera, &LAUBaslerUSBCamera::emitROIChanged, this, &LAUSLICalibrationDialog::onROIChanged, Qt::QueuedConnection);
    connect(camera, &LAUBaslerUSBCamera::emitError, this, [this](QString e) {
        cameraStatusLabel->setText(QString("ERROR: %1").arg(e));
    }, Qt::QueuedConnection);
    cameraThread.start();

    QMetaObject::invokeMethod(camera, "onUpdateExposure", Qt::QueuedConnection, Q_ARG(int, exposureSpin->value()));
    cameraStatusLabel->setText(QString("%1 %2 (%3x%4)").arg(camera->make(), camera->model()).arg(camera->width()).arg(camera->height()));
#else
    cameraStatusLabel->setText(QString("built without USEBASLERUSBCAMERA"));
    QMessageBox::warning(this, windowTitle(), QString("This build has no Basler camera support (define USEBASLERUSBCAMERA / link pylon)."));
#endif
}

/****************************************************************************/
void LAUSLICalibrationDialog::onShowRampWindow()
{
    const QList<QScreen *> screens = QGuiApplication::screens();
    int idx = screenCombo->currentIndex();
    if (idx >= 0 && idx < screens.count()) {
        rampWindow->setGeometry(screens.at(idx)->geometry());
    }
    rampWindow->onSetLevel(0);
    rampWindow->showFullScreen();
}

/****************************************************************************/
void LAUSLICalibrationDialog::onRunSweep()
{
#ifdef USEBASLERUSBCAMERA
    if (!camera || !camera->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the camera first."));
        return;
    }
    if (!rampWindow->isVisible()) {
        onShowRampWindow();
    }
    camera->enableHDMITriggering(hdmiTriggerCheck->isChecked());

    toneWidget->onReset();
    sweeping = true;
    sweepLevel = 0;
    setBusy(true);
    onStepSweep();
#else
    QMessageBox::warning(this, windowTitle(), QString("This build has no camera support."));
#endif
}

/****************************************************************************/
void LAUSLICalibrationDialog::onStepSweep()
{
    if (!sweeping) {
        return;
    }
    if (sweepLevel > 255) {
        // DONE
        sweeping = false;
        setBusy(false);
        uploadButton->setEnabled(true);
        QMessageBox::information(this, windowTitle(), QString("Sweep complete. Review the curve, then upload."));
        return;
    }
    // SHOW THE GRAY LEVEL, LET THE PROJECTOR SETTLE, THEN REQUEST ONE GRAB.
    rampWindow->onSetLevel(sweepLevel);
    QTimer::singleShot(latencySpin->value(), this, [this]() {
        if (sweeping) {
            emit emitGrab(grabBuffer);
        }
    });
}

/****************************************************************************/
void LAUSLICalibrationDialog::onMeanPixel(unsigned int frame, unsigned int mean)
{
    Q_UNUSED(frame);
    // TRIGGER-DELAY SWEEP: accumulate every frame's mean at the current delay.
    if (triggerSweeping) {
        curDelayMeans.append((double)mean);
        return;
    }
    if (!sweeping) {
        return;
    }
    // KEY THE MEASUREMENT TO THE GRAY LEVEL WE ARE CURRENTLY DISPLAYING.
    toneWidget->onUpdateGraph((unsigned int)sweepLevel, mean);
    progress->setValue(sweepLevel);
    sweepLevel++;
    onStepSweep();
}

/****************************************************************************/
void LAUSLICalibrationDialog::onUploadCorrection()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    // PERSIST LIVE SWEEP VALUES TO SETTINGS, THEN READ THE INVERSE-RESPONSE CURVE.
    toneWidget->onSave();
    LAUMemoryObject curve = toneWidget->toneCorrectionCurve();
    lastCorrTable = LAUAuBoard::correctionTable(curve);   // remember what we send, for read-back verify
    if (board->uploadCorrectionTable(curve)) {
        refreshBoardStatus();
        QMessageBox::information(this, windowTitle(), QString("256-byte correction table uploaded. Use \"Verify correction (read back)\" to confirm."));
    } else {
        QMessageBox::warning(this, windowTitle(), board->error());
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::onVerifyCorrection()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    QByteArray got = board->readCorrectionTable();
    if (got.size() != 256) {
        QMessageBox::warning(this, windowTitle(), QString("Read-back failed: %1").arg(board->error()));
        return;
    }
    if (lastCorrTable.size() == 256) {
        if (got == lastCorrTable) {
            QMessageBox::information(this, windowTitle(), QString("Verified: all 256 bytes read back match the uploaded correction table."));
        } else {
            int diff = 0, first = -1;
            for (int i = 0; i < 256; i++) {
                if (got.at(i) != lastCorrTable.at(i)) {
                    diff++;
                    if (first < 0) {
                        first = i;
                    }
                }
            }
            QMessageBox::warning(this, windowTitle(), QString("MISMATCH: %1/256 bytes differ (first at index %2).").arg(diff).arg(first));
        }
    } else {
        QMessageBox::information(this, windowTitle(), QString("Read 256 bytes (corr[0]=%1 .. corr[255]=%2). Upload a table first to compare.")
                                                         .arg((quint8)got.at(0)).arg((quint8)got.at(255)));
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::onSaveCurve()
{
    toneWidget->onSave();
    toneWidget->onExport();
}

/****************************************************************************/
void LAUSLICalibrationDialog::onLoadCurve()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    LAUMemoryObject curve = LAUToneCorrectionWidget::toneCorrectionCurve(QString());
    if (curve.isValid()) {
        if (board->uploadCorrectionTable(curve)) {
            refreshBoardStatus();
            QMessageBox::information(this, windowTitle(), QString("Loaded curve uploaded to FPGA."));
        } else {
            QMessageBox::warning(this, windowTitle(), board->error());
        }
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::setBusy(bool busy)
{
    runButton->setEnabled(!busy);
    uploadButton->setEnabled(!busy && !sweeping);
}

/****************************************************************************/
void LAUSLICalibrationDialog::onRunTriggerSweep()
{
#ifdef USEBASLERUSBCAMERA
    if (!camera || !camera->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the camera first."));
        return;
    }
    if (delayStopSpin->value() < delayStartSpin->value()) {
        QMessageBox::warning(this, windowTitle(), QString("Delay stop must be >= delay start."));
        return;
    }

    // PROJECT THE WBWB FLASH (also generates the per-frame pass-through trigger).
    if (!rampWindow->isVisible()) {
        onShowRampWindow();
    }
    camera->enableCalibration(true);
    camera->enableHDMITriggering(true);
    rampWindow->onSetFlashing(true, flashPeriodSpin->value());

    // SHORT EXPOSURE (queued onto the camera worker).
    QMetaObject::invokeMethod(camera, "onUpdateExposure", Qt::QueuedConnection, Q_ARG(int, trigExposureSpin->value()));

    delayPlot->clearData();
    triggerSweeping = true;
    curDelay      = delayStartSpin->value();
    trigDelayStop = delayStopSpin->value();
    trigDelayStep = delayStepSpin->value();
    trigRunButton->setEnabled(false);
    trigStopButton->setEnabled(true);
    trigStatusLabel->setText(QString("configuring ROI..."));

    // setCenterROI -> emitROIChanged -> onROIChanged sizes the buffer and starts the sweep.
    QMetaObject::invokeMethod(camera, "setCenterROI", Qt::QueuedConnection, Q_ARG(double, 1.0 / (double)roiDivisorSpin->value()));
#else
    QMessageBox::warning(this, windowTitle(), QString("This build has no camera support."));
#endif
}

/****************************************************************************/
void LAUSLICalibrationDialog::onROIChanged(unsigned int width, unsigned int height)
{
    // SIZE BOTH GRAB BUFFERS TO THE CURRENT ROI (keeps the linearisation grab valid too).
    grabBuffer    = LAUMemoryObject(width, height, 1, sizeof(unsigned short), 1);
    triggerBuffer = LAUMemoryObject(width, height, 1, sizeof(unsigned short), (unsigned int)framesAvgSpin->value());
    if (triggerSweeping) {
        trigStatusLabel->setText(QString("ROI %1x%2; sweeping...").arg(width).arg(height));
        stepTriggerDelay();
    }
}

/****************************************************************************/
void LAUSLICalibrationDialog::stepTriggerDelay()
{
    if (!triggerSweeping) {
        return;
    }
    if (curDelay > trigDelayStop) {
        triggerSweeping = false;
        rampWindow->onSetFlashing(false);
        trigRunButton->setEnabled(true);
        trigStopButton->setEnabled(false);
        trigStatusLabel->setText(QString("done (%1 points)").arg(delayPlot->count()));
        return;
    }
    curDelayMeans.clear();
#ifdef USEBASLERUSBCAMERA
    QMetaObject::invokeMethod(camera, "setTriggerDelayMicroseconds", Qt::QueuedConnection, Q_ARG(int, curDelay));
#endif
    // GRAB framesAvg FRAMES AT THIS DELAY (means accumulate via onMeanPixel,
    // finalised on the emitBuffer -> onTriggerGrabComplete that follows).
    emit emitGrab(triggerBuffer);
}

/****************************************************************************/
void LAUSLICalibrationDialog::onTriggerGrabComplete(LAUMemoryObject buffer)
{
    Q_UNUSED(buffer);
    if (!triggerSweeping) {
        return;
    }
    // AVERAGE THE PER-FRAME MEANS AT THIS DELAY -> ONE PLOT POINT.
    double sum = 0.0;
    for (int n = 0; n < curDelayMeans.count(); n++) {
        sum += curDelayMeans.at(n);
    }
    double mean = curDelayMeans.isEmpty() ? 0.0 : sum / (double)curDelayMeans.count();
    delayPlot->appendPoint((double)curDelay, mean);
    trigStatusLabel->setText(QString("delay %1 us -> mean %2  (%3 pts)").arg(curDelay).arg(mean, 0, 'f', 1).arg(delayPlot->count()));

    curDelay += trigDelayStep;
    stepTriggerDelay();
}

/****************************************************************************/
void LAUSLICalibrationDialog::onStopTriggerSweep()
{
    triggerSweeping = false;
    rampWindow->onSetFlashing(false);
    trigRunButton->setEnabled(true);
    trigStopButton->setEnabled(false);
    trigStatusLabel->setText(QString("stopped (%1 points)").arg(delayPlot->count()));
}

/****************************************************************************/
void LAUSLICalibrationDialog::onExportTriggerData()
{
    if (delayPlot->count() == 0) {
        QMessageBox::information(this, windowTitle(), QString("No sweep data to export yet."));
        return;
    }
    delayPlot->exportCsv();
}
