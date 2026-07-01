param(
    [string]$LObjectsPath = "C:\LOGO\TIGER3ENT\LObjects.dll"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LObjectsPath -PathType Leaf)) {
    throw "LObjects.dll was not found: $LObjectsPath"
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public sealed class TypeLibraryEnumValue
{
    public string EnumName { get; set; }
    public string Name { get; set; }
    public object Value { get; set; }
}

public static class TypeLibraryInspector
{
    private enum RegKind
    {
        Default = 0,
        Register = 1,
        None = 2
    }

    [DllImport("oleaut32.dll", CharSet = CharSet.Unicode)]
    private static extern int LoadTypeLibEx(
        string fileName,
        RegKind regKind,
        out System.Runtime.InteropServices.ComTypes.ITypeLib typeLibrary);

    public static TypeLibraryEnumValue[] ReadEnum(string fileName, string enumName)
    {
        System.Runtime.InteropServices.ComTypes.ITypeLib typeLibrary;
        Marshal.ThrowExceptionForHR(
            LoadTypeLibEx(fileName, RegKind.None, out typeLibrary));

        var values = new List<TypeLibraryEnumValue>();

        try
        {
            int count = typeLibrary.GetTypeInfoCount();
            for (int index = 0; index < count; index++)
            {
                System.Runtime.InteropServices.ComTypes.TYPEKIND typeKind;
                typeLibrary.GetTypeInfoType(index, out typeKind);
                if (typeKind != System.Runtime.InteropServices.ComTypes.TYPEKIND.TKIND_ENUM)
                {
                    continue;
                }

                System.Runtime.InteropServices.ComTypes.ITypeInfo typeInfo;
                typeLibrary.GetTypeInfo(index, out typeInfo);

                try
                {
                    string currentEnumName;
                    string documentation;
                    int helpContext;
                    string helpFile;
                    typeInfo.GetDocumentation(
                        -1,
                        out currentEnumName,
                        out documentation,
                        out helpContext,
                        out helpFile);

                    if (!string.Equals(
                        currentEnumName,
                        enumName,
                        StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    IntPtr typeAttributePointer;
                    typeInfo.GetTypeAttr(out typeAttributePointer);

                    try
                    {
                        var typeAttribute = (System.Runtime.InteropServices.ComTypes.TYPEATTR)Marshal.PtrToStructure(
                            typeAttributePointer,
                            typeof(System.Runtime.InteropServices.ComTypes.TYPEATTR));

                        for (int variableIndex = 0;
                             variableIndex < typeAttribute.cVars;
                             variableIndex++)
                        {
                            IntPtr variablePointer;
                            typeInfo.GetVarDesc(variableIndex, out variablePointer);

                            try
                            {
                                var variable = (System.Runtime.InteropServices.ComTypes.VARDESC)Marshal.PtrToStructure(
                                    variablePointer,
                                    typeof(System.Runtime.InteropServices.ComTypes.VARDESC));

                                string valueName;
                                typeInfo.GetDocumentation(
                                    variable.memid,
                                    out valueName,
                                    out documentation,
                                    out helpContext,
                                    out helpFile);

                                values.Add(new TypeLibraryEnumValue
                                {
                                    EnumName = currentEnumName,
                                    Name = valueName,
                                    Value = Marshal.GetObjectForNativeVariant(
                                        variable.desc.lpvarValue)
                                });
                            }
                            finally
                            {
                                typeInfo.ReleaseVarDesc(variablePointer);
                            }
                        }
                    }
                    finally
                    {
                        typeInfo.ReleaseTypeAttr(typeAttributePointer);
                    }
                }
                finally
                {
                    Marshal.ReleaseComObject(typeInfo);
                }
            }
        }
        finally
        {
            Marshal.ReleaseComObject(typeLibrary);
        }

        return values.ToArray();
    }
}
'@

$values = [TypeLibraryInspector]::ReadEnum(
    $LObjectsPath,
    "DataObjectType"
)

if ($values.Count -eq 0) {
    throw "DataObjectType was not found in the LObjects type library."
}

$values |
    Sort-Object { [int]$_.Value } |
    Format-Table Name, Value -AutoSize
