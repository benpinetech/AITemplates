using System;
using System.Collections.Generic;
using System.Text;
using DocumentGenerator.Models;

namespace DocumentGenerator.Models;

public class TemplateLogger
{
    public List<TemplateLogRow> LogRows { get; } = new List<TemplateLogRow>(); public bool DisplayLogsToConsole { get; }

    public TemplateLogger(bool displayLogsToConsole = false)
    {
        DisplayLogsToConsole = displayLogsToConsole;
    }

    public void AddLog(string function, string message, string type)
    {
        var logRow = new TemplateLogRow(function, message, type);
        LogRows.Add(logRow);
        if (DisplayLogsToConsole)
        {
            Console.WriteLine($"{logRow.Function} - {logRow.Message}");
        }
    }

}