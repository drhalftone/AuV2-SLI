#include "lautonecorrectionwidget.h"

#define AVERAGESAMPLES

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
LAUToneCorrectionWidget::LAUToneCorrectionWidget(int frms, QWidget *parent) : QWidget(parent)
{
    this->setLayout(new QVBoxLayout());
    this->layout()->setContentsMargins(6, 6, 6, 6);
    this->setMinimumSize(320, 120);

    label = new LAUToneGraphLabel(frms);
    this->layout()->addWidget(label);

    // CREATE A CONTEXT MENU FOR TOGGLING TEXTURE
    contextMenu = new QMenu();
    QAction *action = contextMenu->addAction(QString("Reset"));
    connect(action, &QAction::triggered, this, &LAUToneCorrectionWidget::onReset);

    action = contextMenu->addAction(QString("Export"));
    connect(action, &QAction::triggered, this, &LAUToneCorrectionWidget::onSave);
    connect(action, &QAction::triggered, this, &LAUToneCorrectionWidget::onExport);
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
LAUMemoryObject LAUToneCorrectionWidget::toneCorrectionCurve(QString filename)
{
    if (filename.isEmpty()) {
        // ASK THE USER TO LOAD A TONE CORRECTION CURVE
        QSettings settings;
        QString directory = settings.value("LAUToneGraphLabel::lastUsedDirectory", QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation)).toString();
        filename = QFileDialog::getOpenFileName(0, QString("Load scan from disk (*.tcc)"), directory, QString("*.tcc"));
    }

    if (filename.isEmpty()) {
        return (LAUMemoryObject());
    } else {
        return (LAUMemoryObject(filename));
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUToneGraphLabel::onUpdateGraph(QVector<unsigned int> vals)
{
    for (int chn = 0; chn < values.count() && chn < vals.count(); chn++) {
        values[chn] = vals[chn];
    }

    // UPDATE THE MINIMUM AND MAXIMUM VALLUES
    minValue = UINT_MAX;
    maxValue = 0.0;
    for (unsigned int n = 0; n < numChns; n++) {
        minValue = qMin(minValue, values[n]);
        maxValue = qMax(maxValue, values[n]);
    }

    // TELL THE WIDGET TO UPDATE ITSELF
    update();
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUToneGraphLabel::onUpdateGraph(unsigned int channel, unsigned int val)
{
#ifdef AVERAGESAMPLES
    // ADD VALUE TO APPROPRIATE LIST
    if (channel >= numChns) {
        return;
    }
    lists[channel] << val;

    // UPDATE THE AVERAGE VALUE FOR THE APPROPRIATE CHANNEL
    float cumSum = 0.0f;
    for (int n = 0; n < lists[channel].count(); n++) {
        cumSum += lists[channel].at(n);
    }
    values[channel] = cumSum / lists[channel].count();
#else
    values[channel] = val;
#endif

    // UPDATE THE MINIMUM AND MAXIMUM VALLUES
    minValue = 1e6;
    maxValue = 0.0;
    for (unsigned int n = 0; n < numChns; n++) {
        minValue = qMin(minValue, values[n]);
        maxValue = qMax(maxValue, values[n]);
    }

    // TELL THE WIDGET TO UPDATE ITSELF
    update();
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUToneGraphLabel::onExport()
{
    // ASK USER TO SAVE A TONE CORRECTION CURVE TO DISK
    QSettings settings;
    QString directory = settings.value("LAUToneGraphLabel::lastUsedDirectory", QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation)).toString();
    QString filename = QFileDialog::getSaveFileName(0, QString("Save projector calibration table (*.tcc)"), directory, QString("*.tcc"));
    if (filename.isEmpty() == false) {
        if (filename.toLower().endsWith(".tcc") == false) {
            filename.append(".tcc");
        }
        settings.setValue("LAUToneGraphLabel::lastUsedDirectory", QFileInfo(filename).absolutePath());
        toneCorrectionCurve().save(filename);
    }
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUToneGraphLabel::onReset()
{
    // CLEAR ALL THE LISTS AND RESET THE VALUES TO ZERO
    for (unsigned int n = 0; n < numChns; n++) {
        lists[n].clear();
        values[n] = 0.0f;
    }
    minValue = 0.0f;
    maxValue = 1.0f;
    update();
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
void LAUToneGraphLabel::onSave()
{
    QSettings settings;
    settings.beginWriteArray(QString("LAUToneGraphLabel::values"), numChns);
    for (unsigned int n = 0; n < numChns; n++) {
        settings.setArrayIndex(n);
        settings.setValue(QString("values"), values[n]);
    }
    settings.endArray();
}

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
LAUMemoryObject LAUToneGraphLabel::toneCorrectionCurve()
{
    //  WE ARE GOING TO READ OUR MEASURED TONE CURVE FROM SETTINGS
    QSettings settings;

    // OPEN THE READ ARRAY AND GET THE VECTOR SIZE
    int numChns = settings.beginReadArray(QString("LAUToneGraphLabel::values"));

    // CREATE A VECTOR TO HOLD THE TONE REPRODUCTION CURVE FROM SETTINGS
    // BEING SURE TO FLIP THE VALUES FROM LEFT TO RIGHT
    QVector<float> values(numChns, 0.0f);
    float minVal = 1e6f;
    float maxVal = 0.0f;
    for (int n = 0; n < numChns; n++) {
        settings.setArrayIndex(n);
        values[numChns - 1 - n] = settings.value(QString("values"), (float)n / (float)(numChns - 1)).toFloat();
        minVal = qMin(minVal, values[numChns - 1 - n]);
        maxVal = qMax(maxVal, values[numChns - 1 - n]);
    }
    settings.endArray();

    // CREATE MEMORY OBJECT TO HOLD THE TONE CORRECTION CURVE
    LAUMemoryObject object(numChns, 1, 1, sizeof(float));

    // BUILD A SCALED VERSION OF THE CURRENT VALUES VECTOR
    QVector<float> map(numChns, 0.0f);
    for (int n = 0; n < numChns; n++) {
        map[n] = (values[n] - minVal) / (maxVal - minVal);
    }

    // NOW ITERATE THROUGH EACH LEVEL LOOKING FOR NEAREST VALUE
    // AND SAVE THE INDEX IN THAT VALUE VECTOR AS THE PIXEL VALUE
    for (int g = 0; g < numChns; g++) {
        float gray = (float)g / (float)(numChns - 1);
        float optDst = 10.0f;
        for (int n = 0; n < numChns; n++) {
            float dist = qAbs(gray - map[n]);
            if (dist < optDst) {
                optDst = dist;
                ((float *)object.pointer())[g] = (float)n / (float)(numChns - 1);
            }
        }
    }
    return (object);
}
