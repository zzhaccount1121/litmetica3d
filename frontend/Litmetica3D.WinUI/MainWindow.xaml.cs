using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Numerics;
using System.Text;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Dispatching;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;

namespace Litmetica3D.WinUI;

public sealed partial class MainWindow : Window
{
    private readonly ObservableCollection<string> files = [];
    private readonly Dictionary<string, ComboBox> choices = [];
    private readonly Dictionary<string, NumberBox> numbers = [];
    private readonly Dictionary<string, CheckBox> checks = [];
    private readonly List<Control> pages = [];
    private readonly List<JsonElement> reports = [];
    private readonly UserSettings settings = UserSettings.Load();
    private readonly TextBox output = new() { PlaceholderText = "选择输出文件夹", HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TextBox regions = new() { Header = "区域筛选", PlaceholderText = "留空转换全部；多个区域用英文逗号分隔（仅单文件）" };
    private readonly TextBox emissionConfig = new() { Header = "自定义发光规则", PlaceholderText = "可选 .json 文件" };
    private readonly TextBox python = new() { Header = "Python 解释器", PlaceholderText = "留空使用内置环境；也可填写 python.exe 的完整路径" };
    private readonly TextBlock presetLabel = new() { FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
    private readonly TextBlock summary = new() { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, Opacity = 0.75 };
    private readonly ScrollViewer summaryScroll = new()
    {
        Height = 240, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        VerticalScrollMode = ScrollMode.Enabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        HorizontalScrollMode = ScrollMode.Disabled, Padding = new Thickness(0, 0, 10, 0),
    };
    private readonly TextBox log = new() { AcceptsReturn = true, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, MinHeight = 200 };
    private readonly TextBox reportText = new() { AcceptsReturn = true, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, MinHeight = 200 };
    private readonly ListView fileList = new() { SelectionMode = ListViewSelectionMode.Multiple, MinHeight = 110, MaxHeight = 210 };
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer logTimer = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private readonly Stopwatch watch = new();
    private readonly StringBuilder logBuffer = new();
    private static readonly JsonSerializerOptions ReportJsonOptions = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };
    private const int ReportPageLength = 24000;
    private readonly List<(int Start, int Length)> reportPages = [];
    private readonly TextBlock reportPageLabel = new() { FontSize = 12, Opacity = 0.7, VerticalAlignment = VerticalAlignment.Center };
    private Button reportPrevious = null!;
    private Button reportNext = null!;
    private Pivot activityTabs = null!;
    private Control visualGroup = null!;
    private Control printGroup = null!;
    private Button emissionBrowse = null!;
    private TextBlock printOnlyNote = null!;
    private string? formattedReport;
    private int reportRevision;
    private int reportPreparingRevision = -1;
    private int reportPage;
    private bool logDirty;
    private DateTime lastEngineEventUtc;
    private CancellationTokenSource? cancellation;
    private EngineClient? engine;
    private bool updating;
    private bool busy;
    private int navigationRevision;
    private int visiblePage = -1;
    private int selectedPage;
    private Vector3? indicatorTarget;
    private readonly Windows.UI.ViewManagement.UISettings uiSettings = new();
    private readonly Vector3Transition indicatorTransition = new() { Duration = TimeSpan.FromMilliseconds(180) };

    public MainWindow(IEnumerable<string>? initialFiles = null)
    {
        InitializeComponent();
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1180, 900));
        output.Text = settings.OutputDirectory;
        python.Text = settings.Python;
        if (Enum.TryParse<ElementTheme>(settings.Theme, out var theme)) Root.RequestedTheme = theme;
        var advanced = BuildAdvanced();
        pages.Add(BuildProject(advanced));
        pages.Add(BuildActivity());
        pages.Add(BuildSettings());
        foreach (var page in pages) { page.Visibility = Visibility.Collapsed; PageHost.Children.Add(page); }
        Header.LayoutUpdated += (_, _) => UpdateNavigationIndicator(animate: false);
        files.CollectionChanged += (_, _) =>
        {
            UpdateFileState();
            if (!busy) Status.Text = files.Count == 0 ? "添加投影，开始创作" : $"已就绪 · {files.Count} 个投影";
        };
        output.TextChanged += (_, _) => UpdateFileState();
        NavigateTo(0);
        ApplyPreset("print");
        if (initialFiles != null) AddFiles(initialFiles.Where(File.Exists));
        timer.Tick += (_, _) => RefreshElapsed();
        logTimer.Tick += (_, _) => FlushLog();
        Closed += (_, _) => { cancellation?.Cancel(); engine?.Dispose(); timer.Stop(); logTimer.Stop(); };
        AppWindow.Closing += (_, args) =>
        {
            if (!busy) return;
            args.Cancel = true;
            ShowNotice("转换正在进行", "请先取消转换，完成后再关闭窗口。", InfoBarSeverity.Informational);
        };
    }

    private void SaveSettings()
    {
        try { settings.OutputDirectory = output.Text.Trim(); settings.Python = python.Text.Trim(); settings.Save(); }
        catch (Exception ex) { ShowError(ex); }
    }
    private void Navigate(object sender, RoutedEventArgs args)
    {
        if (sender is ToggleButton button && int.TryParse(button.Tag?.ToString(), out var index)) NavigateTo(index);
    }
    private void NavigateTo(int index)
    {
        if (index < 0 || index >= pages.Count) return;
        selectedPage = index;
        var tabs = new[] { ConvertTab, ActivityTab, SettingsTab };
        for (var i = 0; i < tabs.Length; i++) tabs[i].IsChecked = i == index;
        UpdateNavigationIndicator(animate: true);
        var revision = ++navigationRevision;
        DispatcherQueue.TryEnqueue(DispatcherQueuePriority.Low, () =>
        {
            if (revision != navigationRevision || visiblePage == index) return;
            if (visiblePage >= 0) pages[visiblePage].Visibility = Visibility.Collapsed;
            pages[index].Visibility = Visibility.Visible;
            visiblePage = index;
        });
    }

    private void UpdateNavigationIndicator(bool animate)
    {
        var item = new[] { ConvertTab, ActivityTab, SettingsTab }[selectedPage];
        if (!item.IsLoaded || item.ActualHeight <= 0) return;
        var position = item.TransformToVisual(Root).TransformPoint(
            new Windows.Foundation.Point((item.ActualWidth - 24) / 2, item.ActualHeight + 5));
        var target = new Vector3((float)position.X, (float)position.Y, 0);
        if (indicatorTarget is { } previous && Vector3.DistanceSquared(previous, target) < 0.01f) return;
        NavigationIndicator.TranslationTransition = animate && indicatorTarget.HasValue && uiSettings.AnimationsEnabled
            ? indicatorTransition : null;
        NavigationIndicator.Translation = target;
        indicatorTarget = target;
        NavigationIndicator.Opacity = 1;
    }
    private string Value(string key) => ((ComboBoxItem)choices[key].SelectedItem).Tag.ToString()!;
    private void Set(string key, string value) => choices[key].SelectedItem = choices[key].Items.Cast<ComboBoxItem>().First(i => (string)i.Tag == value);
    private void ApplyPreset(string preset)
    {
        if (busy) return;
        if (preset == "custom") { SelectPreset("custom"); return; }
        updating = true;
        var print = preset == "print";
        Set("output_format", print ? "stl" : "obj"); Set("geometry", print ? "print" : "visual");
        Set("water", preset == "visual" ? "level" : "drop"); Set("fallback", "ignore");
        Set("components", print ? "main" : "keep"); Set("cavities", print ? "fill" : "preserve");
        Set("boolean_fallback", "voxel32"); Set("blender_lights", preset == "visual" ? "material" : "exact");
        numbers["scale"].Value = 1; numbers["minimum_thickness"].Value = 1.0 / 16;
        numbers["min_component_volume"].Value = 1.0 / 4096; numbers["emission_strength"].Value = 1;
        foreach (var check in checks.Values) check.IsChecked = false;
        checks["seamless_glass"].IsChecked = preset == "render";
        regions.Text = ""; emissionConfig.Text = "";
        updating = false;
        Sync();
        SelectPreset(preset);
    }
    private void Changed()
    {
        if (updating) return;
        Sync(); SelectPreset("custom");
    }
    private void Sync()
    {
        updating = true;
        var stl = Value("output_format") == "stl";
        if (stl) Set("geometry", "print");
        choices["geometry"].IsEnabled = !stl;
        var visual = !stl && Value("geometry") == "visual";
        visualGroup.IsEnabled = visual;
        numbers["minimum_thickness"].IsEnabled = !visual;
        foreach (var key in new[] { "components", "cavities", "boolean_fallback" })
            choices[key].IsEnabled = !visual;
        printOnlyNote.Visibility = visual ? Visibility.Visible : Visibility.Collapsed;
        checks["textures"].IsChecked = visual;
        checks["textures"].IsEnabled = false;
        checks["solid_textures"].IsEnabled = visual;
        numbers["min_component_volume"].IsEnabled = !visual && Value("components") == "remove-small";
        var emission = visual && Value("blender_lights") != "none";
        numbers["emission_strength"].IsEnabled = emission; emissionConfig.IsEnabled = emission;
        emissionBrowse.IsEnabled = emission;
        summary.Text = BuildConfigurationSummary();
        updating = false;
    }

    private string BuildConfigurationSummary()
    {
        var text = new StringBuilder();
        var visual = Value("geometry") == "visual";
        var emission = visual && Value("blender_lights") != "none";
        void Line(object label, string value, string note = "") =>
            text.Append(label).Append("：").Append(value).AppendLine(note.Length == 0 ? "" : $"（{note}）");
        string Toggle(CheckBox check) => check.IsChecked == true ? "开启" : "关闭";
        string NumberValue(NumberBox number) => double.IsFinite(number.Value)
            ? number.Value.ToString("G", System.Globalization.CultureInfo.InvariantCulture) : "待填写有效数值";

        text.AppendLine("模型与输出");
        foreach (var (key, combo) in choices.Where(item => item.Key != "blender_lights"))
        {
            var label = ((ComboBoxItem)combo.SelectedItem).Content.ToString() ?? "";
            var note = visual && (key is "components" or "cavities" or "boolean_fallback")
                ? "仅打印模式生效；当前不执行" : "";
            Line(combo.Header, label, note);
        }
        Line("面数优化", "安全优化（自动）");
        Line(checks["solid_textures"].Content, Toggle(checks["solid_textures"]), visual ? "" : "仅视觉模式生效");

        text.AppendLine().AppendLine("尺寸与基础选项");
        foreach (var (key, number) in numbers.Where(item => item.Key != "emission_strength"))
        {
            var note = key == "minimum_thickness" && visual ? "仅打印模式生效"
                : key == "min_component_volume" && (visual || Value("components") != "remove-small")
                    ? "仅打印模式删除较小壳体时生效" : "";
            Line(number.Header, NumberValue(number), note);
        }
        Line(checks["center"].Content, Toggle(checks["center"]));

        text.AppendLine().AppendLine("视觉与发光");
        Line("是否带有贴图", visual ? "是" : "否", "由输出用途自动决定");
        Line(choices["blender_lights"].Header, ((ComboBoxItem)choices["blender_lights"].SelectedItem).Content.ToString() ?? "",
            visual ? "" : "打印模式不发光");
        Line(numbers["emission_strength"].Header, NumberValue(numbers["emission_strength"]), emission ? "" : "当前不生效");
        Line(emissionConfig.Header, string.IsNullOrWhiteSpace(emissionConfig.Text) ? "默认规则" : emissionConfig.Text.Trim(),
            emission ? "" : "当前不生效");
        foreach (var (key, check) in checks.Where(item => item.Key is not "textures" and not "center" and not "solid_textures"))
            Line(check.Content, Toggle(check), visual ? "" : "仅视觉模式生效");

        text.AppendLine().AppendLine("区域");
        Line("转换区域", string.IsNullOrWhiteSpace(regions.Text) ? "全部区域" : regions.Text.Trim());
        return text.ToString().TrimEnd();
    }

    private void InitializePicker(object picker) => WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
    private async Task<IReadOnlyList<StorageFile>> PickFiles(string extension, bool multiple)
    {
        var picker = new FileOpenPicker(); InitializePicker(picker); picker.FileTypeFilter.Add(extension);
        if (multiple) return await picker.PickMultipleFilesAsync();
        var file = await picker.PickSingleFileAsync();
        return file == null ? [] : [file];
    }
    private async void ChooseFiles(object sender, RoutedEventArgs args)
    {
        try { AddFiles((await PickFiles(".litematic", true)).Select(f => f.Path)); } catch (Exception ex) { ShowError(ex); }
    }
    private void AddFiles(IEnumerable<string> paths)
    {
        if (busy) return;
        foreach (var path in paths)
        {
            if (!path.EndsWith(".litematic", StringComparison.OrdinalIgnoreCase)) continue;
            var absolute = Path.GetFullPath(path);
            if (!files.Contains(absolute, StringComparer.OrdinalIgnoreCase)) files.Add(absolute);
        }
        if (files.Count > 0 && string.IsNullOrWhiteSpace(output.Text)) output.Text = Path.GetDirectoryName(files[0])!;
        UpdateFileState();
    }
    private async void ChooseOutput(object sender, RoutedEventArgs args)
    {
        try
        {
            var picker = new FolderPicker(); InitializePicker(picker); picker.FileTypeFilter.Add("*");
            var folder = await picker.PickSingleFolderAsync();
            if (folder != null) { output.Text = folder.Path; SaveSettings(); }
        }
        catch (Exception ex) { ShowError(ex); }
    }
    private string OutputRoot()
    {
        var selected = Path.GetFullPath(output.Text.Trim());
        return Path.GetFileName(selected.TrimEnd(Path.DirectorySeparatorChar))
            .Equals("L3D_output", StringComparison.OrdinalIgnoreCase)
            ? selected : Path.Combine(selected, "L3D_output");
    }
    private async void ChooseEmission(object sender, RoutedEventArgs args)
    {
        try { var picked = await PickFiles(".json", false); if (picked.Count > 0) emissionConfig.Text = picked[0].Path; }
        catch (Exception ex) { ShowError(ex); }
    }
    private async void ReadRegions(object sender, RoutedEventArgs args)
    {
        if (files.Count != 1) { ShowNotice("请选择一个投影", "区域读取与筛选仅适用于单文件。", InfoBarSeverity.Warning); return; }
        var button = (Button)sender; button.IsEnabled = false;
        try
        {
            var root = EngineClient.FindRoot();
            var executable = python.Text.Trim();
            if (executable.Length == 0)
            {
                var bundled = Path.Combine(root, "runtime", "python", "python.exe");
                var local = Path.Combine(root, ".venv", "Scripts", "python.exe");
                executable = File.Exists(bundled) ? bundled : File.Exists(local) ? local : "python.exe";
            }
            var start = new ProcessStartInfo(executable) { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8 };
            start.Environment["PYTHONUTF8"] = "1";
            foreach (var arg in new[] { "-m", "litmetica3d", files[0], "--list-regions" }) start.ArgumentList.Add(arg);
            using var process = Process.Start(start)!;
            var result = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            try { await process.WaitForExitAsync(timeout.Token); }
            catch (OperationCanceledException) { process.Kill(true); throw new TimeoutException("读取区域超时，请手动填写区域名称。"); }
            var text = await result; var diagnostic = await error;
            if (process.ExitCode != 0) throw new InvalidOperationException(diagnostic);
            regions.Text = string.Join(", ", text.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries));
            ShowNotice("区域已读取", regions.Text, InfoBarSeverity.Success);
        }
        catch (Exception ex) { ShowError(ex); }
        finally { button.IsEnabled = true; }
    }
    private Dictionary<string, object> Snapshot()
    {
        var options = new Dictionary<string, object>();
        foreach (var key in choices.Keys) options[key] = Value(key);
        foreach (var (key, number) in numbers)
        {
            if (!double.IsFinite(number.Value)) throw new ArgumentException($"请填写有效的数值：{number.Header}");
            options[key] = number.Value;
        }
        foreach (var (key, check) in checks) options[key] = check.IsChecked == true;
        options["stl_binary"] = true;
        var visual = Value("geometry") == "visual";
        options["textures"] = visual; options["emission"] = visual && Value("blender_lights") != "none";
        options["optimize"] = "safe";
        options["color"] = false;
        options["solid_textures"] = visual && checks["solid_textures"].IsChecked == true;
        if (visual)
        {
            options["components"] = "keep";
            options["cavities"] = "preserve";
            options["boolean_fallback"] = "voxel32";
        }
        options["emission_config"] = (bool)options["emission"] ? emissionConfig.Text.Trim() : "";
        options["regions"] = regions.Text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return options;
    }
    private async void Start(object sender, RoutedEventArgs args)
    {
        if (busy) return;
        try
        {
            if (files.Count == 0) throw new ArgumentException("请先添加 .litematic 投影文件。");
            if (string.IsNullOrWhiteSpace(output.Text)) throw new ArgumentException("请选择输出文件夹。");
            var request = new { files = files.ToArray(), output_dir = output.Text.Trim(), options = Snapshot() };
            SaveSettings(); Notice.IsOpen = false; reports.Clear(); InvalidateReport(); UpdateResults();
            Progress.Value = 0; Progress.IsIndeterminate = false; ProgressLabel.Text = "0%";
            logBuffer.Clear(); log.Text = "";
            SetBusy(true); watch.Restart(); lastEngineEventUtc = DateTime.UtcNow;
            Elapsed.Text = "已用时间 00:00:00"; Heartbeat.Text = "引擎启动中";
            timer.Start(); Status.Text = "正在启动转换引擎…"; AppendLog("开始转换");
            cancellation = new(); engine = new();
            await engine.RunAsync(python.Text.Trim(), request, HandleEvent, cancellation.Token);
        }
        catch (OperationCanceledException)
        {
            Status.Text = "转换已取消";
            AppendLog("转换进程已停止。已完成的模型保留；输出目录中可能残留 .litmetica3d- 临时文件夹。");
        }
        catch (Exception ex) { Status.Text = "转换失败"; AppendLog(ex.Message); ShowError(ex); }
        finally
        {
            timer.Stop(); watch.Stop(); Elapsed.Text = $"总耗时 {watch.Elapsed:hh\\:mm\\:ss}";
            Heartbeat.Text = ""; Progress.IsIndeterminate = false; FlushLog();
            engine?.Dispose(); engine = null; cancellation?.Dispose(); cancellation = null; SetBusy(false);
        }
    }
    private void HandleEvent(JsonElement item)
    {
        // EngineClient reads pipes on a worker; only coalesced UI events enter the dispatcher.
        if (!DispatcherQueue.HasThreadAccess) { DispatcherQueue.TryEnqueue(() => HandleEvent(item)); return; }
        lastEngineEventUtc = DateTime.UtcNow;
        Heartbeat.Text = "引擎运行中";
        var kind = item.GetProperty("type").GetString();
        var text = item.TryGetProperty("text", out var message) ? message.GetString() ?? "" : "";
        switch (kind)
        {
            case "progress":
                Progress.IsIndeterminate = false;
                Progress.Value = Math.Clamp(item.GetProperty("value").GetDouble() * 100, 0, 100);
                Status.Text = text; ProgressLabel.Text = $"{Progress.Value:0}%"; AppendLog(text); break;
            case "log": AppendLog(text); break;
            case "report":
                reports.Add(item.GetProperty("report").Clone());
                InvalidateReport();
                UpdateResults(); AppendLog($"模型已保存：{reports[^1].GetProperty("output_path").GetString()}"); break;
            case "complete":
                Progress.IsIndeterminate = false; Progress.Value = 100; Status.Text = text; AppendLog(text);
                ProgressLabel.Text = "100%"; ShowNotice("转换完成", text, InfoBarSeverity.Success); NavigateTo(1); break;
            case "cancelled": Status.Text = text; AppendLog(text); break;
            case "error": AppendLog(text); break;
        }
    }
    private void Cancel(object sender, RoutedEventArgs args)
    {
        cancellation?.Cancel(); CancelButton.IsEnabled = false; Status.Text = "正在取消转换…";
    }
    private void SetBusy(bool value)
    {
        busy = value; CancelButton.IsEnabled = value;
        CancelButton.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = value || Progress.Value > 0 ? Visibility.Visible : Visibility.Collapsed;
        pages[0].IsEnabled = !value; pages[2].IsEnabled = !value;
        UpdateFileState();
    }
    private void AppendLog(string message)
    {
        logBuffer.Append('[').Append(DateTime.Now.ToString("HH:mm:ss")).Append("] ").AppendLine(message);
        if (logBuffer.Length > 60000) logBuffer.Remove(0, logBuffer.Length - 45000);
        logDirty = true;
        if (!logTimer.IsEnabled) logTimer.Start();
    }
    private void FlushLog()
    {
        if (!logDirty) { logTimer.Stop(); return; }
        log.Text = logBuffer.ToString();
        logDirty = false;
    }
    private void RefreshElapsed()
    {
        if (!busy) return;
        Elapsed.Text = $"已用时间 {watch.Elapsed:hh\\:mm\\:ss}";
        var silence = (DateTime.UtcNow - lastEngineEventUtc).TotalSeconds;
        Heartbeat.Text = silence >= 5 ? $"后台计算中 · {silence:0} 秒无新进度" : "引擎运行中";
        if (silence >= 10 && Progress.Value < 99) Progress.IsIndeterminate = true;
    }
    private void ClearLog()
    {
        logTimer.Stop(); logBuffer.Clear(); logDirty = false; log.Text = "";
    }
    private void InvalidateReport()
    {
        reportRevision++; formattedReport = null; reportPages.Clear(); reportPage = 0;
        reportText.Text = reports.Count == 0 ? "转换报告将在这里显示。" : "点击“转换报告”查看完整 JSON。";
        UpdateReportPageControls();
        if (activityTabs?.SelectedIndex == 2) _ = PrepareReportAsync(reportRevision);
    }
    private async Task PrepareReportAsync(int revision)
    {
        if (reports.Count == 0 || formattedReport != null || reportPreparingRevision == revision) return;
        reportPreparingRevision = revision;
        var snapshot = reports.ToArray();
        reportText.Text = "正在后台整理报告…";
        try
        {
            var json = await Task.Run(() => JsonSerializer.Serialize(snapshot, ReportJsonOptions));
            if (revision != reportRevision) return;
            formattedReport = json; reportPages.Clear();
            for (var start = 0; start < json.Length;)
            {
                var end = Math.Min(start + ReportPageLength, json.Length);
                if (end < json.Length && char.IsHighSurrogate(json[end - 1])) end++;
                reportPages.Add((start, end - start)); start = end;
            }
            ShowReportPage();
        }
        catch (Exception ex)
        {
            if (revision == reportRevision) reportText.Text = $"报告显示失败：{ex.Message}";
        }
        finally
        {
            if (reportPreparingRevision == revision) reportPreparingRevision = -1;
        }
    }
    private void ShowReportPage()
    {
        if (formattedReport == null || reportPages.Count == 0) { UpdateReportPageControls(); return; }
        reportPage = Math.Clamp(reportPage, 0, reportPages.Count - 1);
        var (start, length) = reportPages[reportPage];
        reportText.Text = formattedReport.Substring(start, length);
        UpdateReportPageControls();
    }
    private void UpdateReportPageControls()
    {
        if (reportPrevious == null || reportNext == null) return;
        reportPrevious.IsEnabled = reportPage > 0;
        reportNext.IsEnabled = reportPage + 1 < reportPages.Count;
        reportPageLabel.Text = reportPages.Count == 0 ? "报告按页显示，不省略内容"
            : $"第 {reportPage + 1} / {reportPages.Count} 页 · 另存包含完整报告";
    }
    private async void OpenOutput(object sender, RoutedEventArgs args)
    {
        try
        {
            var target = OutputRoot();
            if (!Directory.Exists(target)) throw new DirectoryNotFoundException("输出文件夹尚不存在；完成一次转换后即可打开。");
            await Windows.System.Launcher.LaunchFolderAsync(await StorageFolder.GetFolderFromPathAsync(target));
        }
        catch (Exception ex) { ShowError(ex); }
    }
    private async void SaveReport(object sender, RoutedEventArgs args)
    {
        try
        {
            if (reports.Count == 0) throw new InvalidOperationException("当前还没有已完成的转换报告。");
            var picker = new FileSavePicker { SuggestedFileName = "litematica-reports" }; InitializePicker(picker);
            picker.FileTypeChoices.Add("JSON 报告", new List<string> { ".json" });
            var file = await picker.PickSaveFileAsync();
            if (file == null) return;
            if (formattedReport == null) await PrepareReportAsync(reportRevision);
            while (formattedReport == null && reportPreparingRevision == reportRevision)
                await Task.Delay(40);
            if (formattedReport != null) await FileIO.WriteTextAsync(file, formattedReport);
        }
        catch (Exception ex) { ShowError(ex); }
    }
    private void ShowError(Exception ex) => ShowNotice("操作未完成", ex.Message, InfoBarSeverity.Error);
    private void ShowNotice(string title, string message, InfoBarSeverity severity)
    {
        Notice.Title = title; Notice.Message = message; Notice.Severity = severity; Notice.IsOpen = true;
    }
}
