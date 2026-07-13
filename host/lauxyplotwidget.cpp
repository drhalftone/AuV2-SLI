/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau -- LAUXYPlotWidget implementation.      *
 *                                                                               *
 *********************************************************************************/

#include "lauxyplotwidget.h"

#include <QPainter>
#include <QPaintEvent>
#include <QFileDialog>
#include <QFile>
#include <QTextStream>
#include <QStandardPaths>
#include <QFileInfo>
#include <QtGlobal>

/****************************************************************************/
LAUXYPlotWidget::LAUXYPlotWidget(QWidget *parent) : QWidget(parent), xLabel(QString("x")), yLabel(QString("y"))
{
    setMinimumSize(360, 240);
    setAutoFillBackground(true);
    QPalette pal = palette();
    pal.setColor(QPalette::Window, Qt::white);
    setPalette(pal);
}

/****************************************************************************/
void LAUXYPlotWidget::setLabels(const QString &x, const QString &y)
{
    xLabel = x;
    yLabel = y;
    update();
}

/****************************************************************************/
void LAUXYPlotWidget::setData(const QVector<double> &x, const QVector<double> &y)
{
    xs = x;
    ys = y;
    update();
}

/****************************************************************************/
void LAUXYPlotWidget::appendPoint(double x, double y)
{
    xs.append(x);
    ys.append(y);
    update();
}

/****************************************************************************/
void LAUXYPlotWidget::clearData()
{
    xs.clear();
    ys.clear();
    update();
}

/****************************************************************************/
void LAUXYPlotWidget::dataBounds(double &xmin, double &xmax, double &ymin, double &ymax) const
{
    if (xs.isEmpty()) {
        xmin = 0.0; xmax = 1.0; ymin = 0.0; ymax = 1.0;
        return;
    }
    xmin = xmax = xs.at(0);
    ymin = ymax = ys.at(0);
    for (int n = 0; n < xs.count(); n++) {
        xmin = qMin(xmin, xs.at(n));
        xmax = qMax(xmax, xs.at(n));
        ymin = qMin(ymin, ys.at(n));
        ymax = qMax(ymax, ys.at(n));
    }
    if (xmax <= xmin) {
        xmax = xmin + 1.0;
    }
    if (ymax <= ymin) {
        ymax = ymin + 1.0;
    }
}

/****************************************************************************/
void LAUXYPlotWidget::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);

    const int L = 60, R = 16, T = 12, B = 42;
    QRectF plot(L, T, width() - L - R, height() - T - B);

    p.fillRect(rect(), Qt::white);
    p.setPen(QPen(Qt::black, 1));
    p.drawRect(plot);

    double xmin, xmax, ymin, ymax;
    dataBounds(xmin, xmax, ymin, ymax);
    double ypad = (ymax - ymin) * 0.05;
    ymin -= ypad;
    ymax += ypad;

    const double xspan = xmax - xmin;
    const double yspan = ymax - ymin;

    QFont f = p.font();
    f.setPointSize(8);
    p.setFont(f);

    // GRIDLINES + TICK LABELS (5 divisions each axis)
    for (int i = 0; i <= 5; i++) {
        double fx = xmin + xspan * i / 5.0;
        double px = plot.left() + (fx - xmin) / xspan * plot.width();
        p.setPen(QPen(QColor(232, 232, 232), 1));
        p.drawLine(QPointF(px, plot.top()), QPointF(px, plot.bottom()));
        p.setPen(Qt::black);
        p.drawText(QRectF(px - 32, plot.bottom() + 3, 64, 16), Qt::AlignHCenter | Qt::AlignTop, QString::number(fx, 'g', 4));

        double fy = ymin + yspan * i / 5.0;
        double py = plot.bottom() - (fy - ymin) / yspan * plot.height();
        p.setPen(QPen(QColor(232, 232, 232), 1));
        p.drawLine(QPointF(plot.left(), py), QPointF(plot.right(), py));
        p.setPen(Qt::black);
        p.drawText(QRectF(2, py - 8, L - 8, 16), Qt::AlignRight | Qt::AlignVCenter, QString::number(fy, 'g', 4));
    }

    // AXIS LABELS
    QFont fb = p.font();
    fb.setPointSize(9);
    p.setFont(fb);
    p.setPen(Qt::black);
    p.drawText(QRectF(plot.left(), height() - 18, plot.width(), 16), Qt::AlignHCenter, xLabel);
    p.save();
    p.translate(14, plot.center().y());
    p.rotate(-90);
    p.drawText(QRectF(-plot.height() / 2, -8, plot.height(), 16), Qt::AlignHCenter, yLabel);
    p.restore();

    // DATA: polyline + points
    if (!xs.isEmpty()) {
        p.setClipRect(plot);
        QPolygonF poly;
        for (int n = 0; n < xs.count(); n++) {
            double px = plot.left()   + (xs.at(n) - xmin) / xspan * plot.width();
            double py = plot.bottom() - (ys.at(n) - ymin) / yspan * plot.height();
            poly << QPointF(px, py);
        }
        if (poly.size() >= 2) {
            p.setPen(QPen(QColor(0, 90, 200), 1.5));
            p.drawPolyline(poly);
        }
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(0, 90, 200));
        for (int n = 0; n < poly.size(); n++) {
            p.drawEllipse(poly.at(n), 2.2, 2.2);
        }
        p.setClipping(false);
    }
}

/****************************************************************************/
bool LAUXYPlotWidget::exportCsv(const QString &filenameIn)
{
    QString filename = filenameIn;
    if (filename.isEmpty()) {
        QString dir = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
        filename = QFileDialog::getSaveFileName(this, QString("Export plot data (*.csv)"), dir, QString("*.csv"));
    }
    if (filename.isEmpty()) {
        return (false);
    }
    if (!filename.toLower().endsWith(QString(".csv"))) {
        filename.append(QString(".csv"));
    }
    QFile file(filename);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        return (false);
    }
    QTextStream s(&file);
    s << xLabel << "," << yLabel << "\n";
    for (int n = 0; n < xs.count(); n++) {
        s << xs.at(n) << "," << ys.at(n) << "\n";
    }
    file.close();
    return (true);
}
