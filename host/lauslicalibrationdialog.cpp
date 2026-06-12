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
      sweeping(false), sweepLevel(0)
{
    setWindowTitle(QString("AuV2-SLI Linearisation"));

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
        QPushButton *applyButton = new QPushButton(QString("Apply SLI control"));
        connect(applyButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onApplySLIControl);
        QPushButton *resetButton = new QPushButton(QString("Reset correction (identity)"));
        connect(resetButton, &QPushButton::clicked, this, &LAUSLICalibrationDialog::onResetCorrection);

        QGridLayout *grid = new QGridLayout();
        grid->addWidget(new QLabel(QString("Port:")), 0, 0);
        grid->addWidget(portCombo, 0, 1);
        grid->addWidget(connectButton, 0, 2);
        grid->addWidget(boardStatusLabel, 1, 0, 1, 3);
        QHBoxLayout *ctrlRow = new QHBoxLayout();
        ctrlRow->addWidget(orientCheck);
        ctrlRow->addWidget(rCheck);
        ctrlRow->addWidget(gCheck);
        ctrlRow->addWidget(bCheck);
        ctrlRow->addWidget(applyButton);
        grid->addLayout(ctrlRow, 2, 0, 1, 3);
        grid->addWidget(resetButton, 3, 0, 1, 3);
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

    // ---- calibration group ----------------------------------------------------
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

    // ---- assemble -------------------------------------------------------------
    QVBoxLayout *layout = new QVBoxLayout();
    layout->addWidget(boardBox);
    layout->addWidget(camBox);
    layout->addWidget(projBox);
    layout->addWidget(toneWidget, 1);
    layout->addWidget(progress);
    layout->addLayout(buttonRow);
    setLayout(layout);

    rampWindow = new LAURampWindow();
    resize(560, 640);
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
    boardStatusLabel->setText(QString("%1  ID=0x%2 VER=0x%3 STATUS=0x%4 FLAGS=0x%5")
                                  .arg(board->portName())
                                  .arg(id < 0 ? 0 : id, 2, 16, QChar('0'))
                                  .arg(ver < 0 ? 0 : ver, 2, 16, QChar('0'))
                                  .arg(status < 0 ? 0 : status, 2, 16, QChar('0'))
                                  .arg(flags < 0 ? 0 : flags, 2, 16, QChar('0')));
}

/****************************************************************************/
void LAUSLICalibrationDialog::onResetCorrection()
{
    if (!board || !board->isValid()) {
        QMessageBox::warning(this, windowTitle(), QString("Connect the board first."));
        return;
    }
    if (board->uploadIdentityCorrection()) {
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
    if (!board->setSLIControl(true, rCheck->isChecked(), gCheck->isChecked(), bCheck->isChecked(), orientCheck->isChecked())) {
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
    if (board->uploadCorrectionTable(curve)) {
        refreshBoardStatus();
        QMessageBox::information(this, windowTitle(), QString("256-byte correction table uploaded. FPGA now renders the linearised sinusoid."));
    } else {
        QMessageBox::warning(this, windowTitle(), board->error());
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
