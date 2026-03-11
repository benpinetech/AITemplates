using PineCone.BuildingBlocks.Data;

namespace DocumentGenerator;

public static class PdfGenerator
{
    public static PdfDocument CreatePdf(string content, ChromePdfRenderOptions? options = null)
    {
        return getPdfRenderer(options).RenderHtmlAsPdf(content);
    }
    
    public static PdfDocument CreatePdfFromRtf(string content, PdfRenderOptions? options = null )
    {
        return getPdfRenderer(options).RenderRtfStringAsPdf(content);
    }

    private static ChromePdfRenderer getPdfRenderer(ChromePdfRenderOptions? options = null)
    {
        var renderer = new ChromePdfRenderer();

        if (options is null)
        {
            options = new ChromePdfRenderOptions();
            options.PrintHtmlBackgrounds = true;
            options.PaperOrientation = IronPdf.Rendering.PdfPaperOrientation.Portrait;
            options.MarginTop = 0;
            options.MarginBottom = 0;
            options.MarginLeft = 0;
            options.MarginRight = 0;
        }

        renderer.RenderingOptions = options;
        return renderer;
    }

    private static ChromePdfRenderer getPdfRenderer(PdfRenderOptions? options = null)
    {
        var renderer = new ChromePdfRenderer();

        ChromePdfRenderOptions chromeOptions = new ChromePdfRenderOptions();
        if (options is not null)
        {
            chromeOptions.PrintHtmlBackgrounds = true;
            chromeOptions.PaperOrientation = IronPdf.Rendering.PdfPaperOrientation.Portrait;
            chromeOptions.MarginTop = options.MarginTopMm.HasValue ? options.MarginTopMm.Value : 0;
            chromeOptions.MarginBottom = options.MarginBottomMm.HasValue ? options.MarginBottomMm.Value : 0;
            chromeOptions.MarginLeft = options.MarginLeftMm.HasValue ? options.MarginLeftMm.Value : 0;
            chromeOptions.MarginRight = options.MarginRightMm.HasValue ? options.MarginRightMm.Value : 0;
        }
        else
        {
            chromeOptions.PrintHtmlBackgrounds = true;
            chromeOptions.PaperOrientation = IronPdf.Rendering.PdfPaperOrientation.Portrait;
            chromeOptions.MarginTop = 0;
            chromeOptions.MarginBottom = 0;
            chromeOptions.MarginLeft = 0;
            chromeOptions.MarginRight = 0;
        }
        renderer.RenderingOptions = chromeOptions;
        return renderer;
    }
}