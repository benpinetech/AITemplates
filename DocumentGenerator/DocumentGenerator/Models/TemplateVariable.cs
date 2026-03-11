namespace DocumentGenerator.Models;

public class TemplateVariable
{
    public int OriginalIndex { get; set; }
    public string RootName { get; set; }
    public string FullCommand { get; set; }
    public string Value { get; set; }

    public TemplateVariable()
    {
        OriginalIndex = -1;
        RootName = string.Empty;
        FullCommand = string.Empty;
        Value = string.Empty;
    }

    public TemplateVariable(int originalIndex, string rootName, string fullCommand)
    {
        OriginalIndex = originalIndex;
        RootName = rootName;
        FullCommand = fullCommand;
    }
}

public class TemplateVariableComparer : IEqualityComparer<TemplateVariable>
{
    public bool Equals(TemplateVariable? x, TemplateVariable? y)
    {
        if (x == null || y == null)
            return false;

        return x.FullCommand == y.FullCommand;
    }

    public int GetHashCode(TemplateVariable obj)
    {
        if (obj == null)
            return 0;

        return HashCode.Combine(obj.FullCommand, obj.Value);
    }
}
