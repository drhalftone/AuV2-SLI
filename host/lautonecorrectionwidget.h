#ifndef LAUTONECORRECTIONWIDGET_H
#define LAUTONECORRECTIONWIDGET_H

#include <QWidget>

#include <QPen>
#include <QMenu>
#include <QBrush>
#include <QLabel>
#include <QWidget>
#include <QPainter>
#include <QSettings>
#include <QVBoxLayout>
#include <QMouseEvent>

#include "laumemoryobject.h"

#define LAUBARGRAPHVECTORLENGTH  256

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
class LAUToneGraphLabel : public QLabel
{
    Q_OBJECT

public:
    LAUToneGraphLabel(unsigned int chn = LAUBARGRAPHVECTORLENGTH, QWidget *parent = nullptr) : QLabel(parent), numChns(chn), maxValue(1.0f), minValue(0.0f)
    {
        lists.resize(numChns);
        values.resize(numChns);
        for (unsigned int n = 0; n < numChns; n++) {
            values[n] = (float)n / (float)(numChns - 1);
            values[n] = values[n] * values[n];
        }
    }

    static LAUMemoryObject toneCorrectionCurve();

public slots:
    void onUpdateGraph(unsigned int channel, unsigned int val);
    void onUpdateGraph(QVector<unsigned int> vals);
    void onExport();
    void onReset();
    void onSave();

protected:
    void paintEvent(QPaintEvent *)
    {
        QPainter painter;
        painter.begin(this);

        // DRAW THE BACKGROUND AS A WHITE FIELD WITH BLACK BORDER
        painter.setBrush(QBrush(QColor(255, 255, 255), Qt::SolidPattern));
        painter.setPen(QPen(QColor(0, 0, 0), 2, Qt::SolidLine));
        painter.drawRect(QRect(0, 0, width(), height()));

        // DRAW THE GRID LINES AS THIN DASHED BLACK LINES
        painter.setPen(QPen(QColor(0, 0, 0), 1, Qt::DashLine));
        for (unsigned int n = 1; n < 5; n++) {
            float lambda = (float)n / 5.0f * (float)height();
            painter.drawLine(0.0f, lambda, (float)width(), lambda);
        }

        // DRAW THE GRID LINES AS THIN DASHED BLACK LINES
        painter.setPen(QPen(QColor(0, 0, 0), 0.5f, Qt::DotLine));
        for (unsigned int n = 0; n < 5; n++) {
            float lambda = ((float)n + 0.5f) / 5.0f * (float)height();
            painter.drawLine(0.0f, lambda, (float)width(), lambda);
        }

        // DRAW THE BAR GRAPH AS BLUE SQUARES WITH THIN SOLID BLACK LINE BORDER
        painter.setBrush(QBrush(QColor(0, 0, 255), Qt::SolidPattern));
        painter.setPen(QPen(QColor(0, 0, 255), 1, Qt::SolidLine));
        for (unsigned int n = 0; n < numChns; n++) {
            float lambda = (values[n] - 0.9 * minValue) / (1.1 * maxValue - 0.9 * minValue);

            float lefEdge = (float)(n + 0) / (float)numChns * (float)width();
            float rigEdge = (float)(n + 1) / (float)numChns * (float)width();
            float topEdge = (1.0f - lambda) * (float)height();
            float botEdge = (float)height();

            painter.drawRect(qRound(lefEdge), qRound(topEdge), qRound(rigEdge - lefEdge), qRound(botEdge - topEdge));
        }
        painter.end();
    }

private:
    QVector<QList<int> > lists;
    QVector<float> values;
    unsigned int numChns;
    float maxValue, minValue;
};

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
class LAUToneCorrectionWidget : public QWidget
{
    Q_OBJECT

public:
    explicit LAUToneCorrectionWidget(int frms = 256, QWidget *parent = nullptr);

    LAUMemoryObject toneCorrectionCurve()
    {
        return (label->toneCorrectionCurve());
    }

    static LAUMemoryObject toneCorrectionCurve(QString filename);

public slots:
    void onUpdateGraph(unsigned int channel, unsigned int value)
    {
        label->onUpdateGraph(channel, value);
    }

    void onUpdateGraph(QVector<unsigned int> values)
    {
        label->onUpdateGraph(values);
    }

    void onExport()
    {
        label->onExport();
    }

    void onReset()
    {
        label->onReset();
    }

    void onSave()
    {
        label->onSave();
    }

protected:
    void mousePressEvent(QMouseEvent *event)
    {
        if (event->button() == Qt::RightButton) {
            if (contextMenu) {
                contextMenu->popup(event->globalPos());
            }
        }
    }

private:
    LAUToneGraphLabel *label;
    QMenu *contextMenu;
};

#endif // LAUTONECORRECTIONWIDGET_H
