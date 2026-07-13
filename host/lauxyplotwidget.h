/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 *                                                                               *
 * LAUXYPlotWidget -- minimal QPainter X/Y line+scatter plot with auto-scaling   *
 * and CSV export. No third-party plotting dependency.                           *
 *                                                                               *
 *********************************************************************************/

#ifndef LAUXYPLOTWIDGET_H
#define LAUXYPLOTWIDGET_H

#include <QWidget>
#include <QVector>
#include <QString>

class LAUXYPlotWidget : public QWidget
{
    Q_OBJECT

public:
    explicit LAUXYPlotWidget(QWidget *parent = nullptr);

    void setLabels(const QString &xLabel, const QString &yLabel);
    void setData(const QVector<double> &x, const QVector<double> &y);
    void appendPoint(double x, double y);     // live append + repaint
    void clearData();

    int count() const
    {
        return (xs.count());
    }

public slots:
    bool exportCsv(const QString &filename = QString());   // prompts if empty

protected:
    void paintEvent(QPaintEvent *) override;

private:
    QVector<double> xs, ys;
    QString xLabel, yLabel;

    void dataBounds(double &xmin, double &xmax, double &ymin, double &ymax) const;
};

#endif // LAUXYPLOTWIDGET_H
