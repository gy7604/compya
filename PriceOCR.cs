using System;
using System.Threading.Tasks;
using Windows.Storage;
using Windows.Storage.Streams;
using Windows.Graphics.Imaging;
using Windows.Globalization;
using Windows.Media.Ocr;
using Windows.Foundation;

// Local Windows OCR only. No network, credentials, or system configuration.
class PriceOCR {
    static int Main(string[] args) {
        try {
            Console.OutputEncoding=new System.Text.UTF8Encoding(false);
            Run(args[0],args.Length>1 ? args[1] : null); return 0;
        }
        catch(Exception error) { Console.Error.WriteLine(error.Message); return 1; }
    }
    static T Wait<T>(IAsyncOperation<T> operation) {
        var info=(IAsyncInfo)operation;
        var watch=System.Diagnostics.Stopwatch.StartNew();
        while(info.Status==AsyncStatus.Started) {
            if(watch.ElapsedMilliseconds>8000) { info.Cancel(); throw new Exception("OCR timeout"); }
            System.Threading.Thread.Sleep(10);
        }
        if(info.Status!=AsyncStatus.Completed)throw new Exception("OCR operation failed: "+typeof(T).Name+" "+info.ErrorCode);
        return operation.GetResults();
    }
    static void Run(string path,string language) {
        var engine=OcrEngine.TryCreateFromLanguage(new Language(language ?? "en-US"));
        if(engine==null && language==null)engine=OcrEngine.TryCreateFromUserProfileLanguages();
        if(engine==null)throw new Exception("OCR unavailable");
        var file=Wait(StorageFile.GetFileFromPathAsync(path));
        using(var stream=Wait(file.OpenAsync(FileAccessMode.Read))) {
            var decoder=Wait(BitmapDecoder.CreateAsync(stream));
            using(var bitmap=Wait(decoder.GetSoftwareBitmapAsync())) {
                var result=Wait(engine.RecognizeAsync(bitmap));
                Console.WriteLine(result.Text);
            }
        }
    }
}
