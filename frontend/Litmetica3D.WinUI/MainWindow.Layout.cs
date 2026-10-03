using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;

namespace Litmetica3D.WinUI;

public sealed partial class MainWindow
{
    private readonly Dictionary<string, RadioButton> presetButtons = [];
    private readonly Dictionary<string, Border> presetTiles = [];
    private readonly TextBlock fileCount = new() { FontSize = 12, Opacity = 0.65 };
    private readonly TextBlock resultCount = new() { FontSize = 13, Opacity = 0.7 };
    private readonly StackPanel resultCards = new() { Spacing = 12 };
    private FrameworkElement emptyFiles = null!;
    private FrameworkElement fileActions = null!;
    private FrameworkElement emptyResults = null!;
    private Expander advancedOptions = null!;
    private Grid workspace = null!;

    private static StackPanel Stack(params UIElement[] children)
    {
        var panel = new StackPanel { Spacing = 16 };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }
    private static TextBlock Text(string value, double size = 14) => new()
    {
        Text = value, FontSize = size, TextWrapping = TextWrapping.Wrap,
    };
    private static TextBlock Muted(string value, double size = 13)
    {
        var text = Text(value, size);
        text.Opacity = 0.65;
        return text;
    }
    private static FontIcon Icon(string glyph, double size = 20) => new() { Glyph = glyph, FontSize = size };
    private static Border Divider() => new() { Height = 1, Background = new SolidColorBrush(Microsoft.UI.Colors.Gray), Opacity = 0.15 };
    private ContentControl Card(string title, string description, params UIElement[] children)
    {
        var heading = Text(title, 17);
        heading.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        var panel = Stack(heading);
        if (description.Length > 0) panel.Children.Add(Muted(description));
        foreach (var child in children) panel.Children.Add(child);
        return new ContentControl { HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch,
            Content = new Border { Style = (Style)Root.Resources["CardStyle"], Child = panel } };
    }
    private static ScrollViewer Scroll(UIElement content) => new()
    {
        Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 8, 4),
    };
    private static Button Button(string label, RoutedEventHandler action)
    {
        var button = new Button { Content = label };
        button.Click += action;
        return button;
    }
    private static StackPanel Row(params UIElement[] children)
    {
        var panel = Stack(children); panel.Orientation = Orientation.Horizontal; panel.Spacing = 8;
        return panel;
    }
    private static Grid Pair(FrameworkElement first, FrameworkElement second)
    {
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        grid.Children.Add(first); Grid.SetColumn(second, 1); grid.Children.Add(second);
        second.VerticalAlignment = VerticalAlignment.Bottom;
        return grid;
    }
    private static StackPanel PageHeading(string title, string description)
    {
        var heading = Text(title, 30); heading.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        var panel = Stack(heading, Muted(description, 14)); panel.Spacing = 8; panel.Margin = new Thickness(0, 0, 0, 8);
        return panel;
    }
    private static Grid Fields(params FrameworkElement[] fields)
    {
        var grid = new Grid { ColumnSpacing = 20, RowSpacing = 16 };
        grid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        for (var i = 0; i < fields.Length; i++)
        {
            if (i % 2 == 0) grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
            grid.Children.Add(fields[i]); Grid.SetRow(fields[i], i / 2); Grid.SetColumn(fields[i], i % 2);
        }
        grid.SizeChanged += (_, e) =>
        {
            var columns = e.NewSize.Width >= 620 ? 2 : 1;
            var rows = (fields.Length + columns - 1) / columns;
            while (grid.RowDefinitions.Count < rows) grid.RowDefinitions.Add(new() { Height = GridLength.Auto });
            while (grid.RowDefinitions.Count > rows) grid.RowDefinitions.RemoveAt(grid.RowDefinitions.Count - 1);
            grid.ColumnDefinitions[1].Width = columns == 1 ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            for (var i = 0; i < fields.Length; i++) { Grid.SetRow(fields[i], i / columns); Grid.SetColumn(fields[i], i % columns); }
        };
        return grid;
    }
    private Expander Group(string title, string detail, UIElement body, bool expanded = false)
    {
        var heading = Text(title, 17);
        heading.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        return new Expander
        {
            // The native header has a 16px left inset and no vertical padding.
            // Add space for two lines while preserving its keyboard and theme behavior.
            Header = new StackPanel
            {
                Spacing = 6, Margin = new Thickness(8, 16, 0, 16),
                VerticalAlignment = VerticalAlignment.Center,
                Children = { heading, Muted(detail, 13) },
            },
            Content = body, IsExpanded = expanded, MinHeight = 80,
            CornerRadius = new CornerRadius(12), Padding = new Thickness(24),
            HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
        };
    }

    private Control BuildProject(UIElement advanced)
    {
        fileList.ItemsSource = files;
        fileList.ItemTemplate = (DataTemplate)Root.Resources["FileTemplate"];
        fileList.MinHeight = 0; fileList.MaxHeight = 236;
        fileList.HorizontalContentAlignment = HorizontalAlignment.Stretch;
        fileList.SelectionChanged += (_, _) => UpdateFileActions();
        var art = new Viewbox { Width = 48, Height = 48, HorizontalAlignment = HorizontalAlignment.Center,
            Child = new ContentControl { Content = "cube", ContentTemplate = (DataTemplate)Root.Resources["CubeTemplate"] } };
        var emptyTitle = Text("拖入你的投影文件", 19); emptyTitle.HorizontalAlignment = HorizontalAlignment.Center;
        var hint = Muted(".litematic · 支持批量转换"); hint.HorizontalAlignment = HorizontalAlignment.Center;
        var add = Button("选择文件", ChooseFiles); add.HorizontalAlignment = HorizontalAlignment.Center;
        emptyFiles = new StackPanel { Spacing = 10, Children = { art, emptyTitle, hint, add } };
        emptyFiles.Margin = new Thickness(12, 4, 12, 4);
        var remove = Button("移除所选", (_, _) =>
        {
            foreach (var path in fileList.SelectedItems.Cast<string>().ToArray()) files.Remove(path);
        });
        remove.Tag = "remove";
        fileActions = Row(Button("添加文件", ChooseFiles), remove);
        var queue = Card("投影文件", "", Pair(fileCount, Muted("在此拖放文件")), emptyFiles, fileList, fileActions);
        queue.AllowDrop = true;
        queue.DragOver += (_, e) =>
        {
            e.AcceptedOperation = !busy && e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)
                ? Windows.ApplicationModel.DataTransfer.DataPackageOperation.Copy : Windows.ApplicationModel.DataTransfer.DataPackageOperation.None;
        };
        queue.Drop += async (_, e) =>
        {
            if (busy || !e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)) return;
            try { AddFiles((await e.DataView.GetStorageItemsAsync()).OfType<Windows.Storage.StorageFile>().Select(f => f.Path)); }
            catch (Exception ex) { ShowError(ex); }
        };
        output.PlaceholderText = "模型保存到哪里？";
        var outputCard = Card("保存位置", "", Pair(output, Button("浏览", ChooseOutput)),
            Muted("自动建立 L3D_output，并按投影名称分文件夹保存。", 12));
        var left = new Grid { RowSpacing = 16 };
        left.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        left.RowDefinitions.Add(new() { Height = GridLength.Auto });
        left.Children.Add(queue); left.Children.Add(outputCard); Grid.SetRow(outputCard, 1);
        var modes = Stack(
            Preset("print", "\uE749", "3D 打印", "封闭实体，适合切片与打印", "STL"),
            Preset("visual", "\uE8B9", "彩色模型", "原版贴图，适合三维软件", "OBJ"),
            Preset("render", "\uE722", "Blender 渲染", "透明玻璃与可编辑灯光", "OBJ"),
            Preset("custom", "\uE70F", "自定义", "显示当前高级配置", "自定义"));
        modes.Spacing = 18;
        presetLabel.FontSize = 13;
        summary.FontSize = 13;
        summary.LineHeight = 23;
        summaryScroll.Content = summary;
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(summaryScroll, "完整参数配置，可滚动查看");
        var right = Card("预设集", "选择适合用途的配置，也可在下方自定义。", modes, Divider(), presetLabel, summaryScroll);
        workspace = new Grid { ColumnSpacing = 20 };
        workspace.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        workspace.ColumnDefinitions.Add(new() { Width = new GridLength(340) });
        workspace.RowDefinitions.Add(new() { Height = GridLength.Auto });
        workspace.RowDefinitions.Add(new() { Height = GridLength.Auto });
        workspace.Children.Add(left); workspace.Children.Add(right); Grid.SetColumn(right, 1);
        workspace.SizeChanged += (_, e) =>
        {
            var wide = e.NewSize.Width >= 820;
            workspace.ColumnDefinitions[1].Width = new GridLength(wide ? 340 : 0);
            workspace.RowSpacing = wide ? 0 : 16;
            Grid.SetColumn(right, wide ? 1 : 0); Grid.SetRow(right, wide ? 0 : 1);
        };
        advancedOptions = Group("自定义参数", "调整几何、贴图、灯光与转换区域", advanced);
        var content = Stack(PageHeading("把方块，变成作品。", "从 Minecraft 投影到可打印、可渲染的三维模型。"), workspace, advancedOptions);
        UpdateFileState();
        return Scroll(content);
    }
    private Border Preset(string key, string glyph, string title, string subtitle, string format)
    {
        var name = Text(title, 15); name.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        var badge = Muted(format, 11); badge.VerticalAlignment = VerticalAlignment.Center;
        // Match the native radio indicator's 32px first row.
        var titleRow = Pair(name, badge); titleRow.MinHeight = 32;
        name.VerticalAlignment = badge.VerticalAlignment = VerticalAlignment.Center;
        var copy = Stack(titleRow, Muted(subtitle, 12)); copy.Spacing = 0;
        var content = new Grid { ColumnSpacing = 12 };
        content.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        content.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        var icon = Icon(glyph, 22); icon.VerticalAlignment = VerticalAlignment.Top; icon.Margin = new Thickness(0, 5, 0, 0);
        content.Children.Add(icon); content.Children.Add(copy); Grid.SetColumn(copy, 1);
        var button = new RadioButton { Content = content, GroupName = "ExportPreset", HorizontalContentAlignment = HorizontalAlignment.Stretch,
            HorizontalAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Top, Padding = new Thickness(12, 0, 0, 0) };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, title);
        button.Checked += (_, _) => { if (!updating) ApplyPreset(key); };
        presetButtons[key] = button;
        var tile = new Border { Style = (Style)Root.Resources["PresetTileStyle"], Child = button };
        tile.Tapped += (_, _) => button.IsChecked = true;
        presetTiles[key] = tile;
        return tile;
    }
    private void UpdateFileActions()
    {
        if (fileActions is StackPanel actions)
            foreach (var button in actions.Children.OfType<Button>())
                if (button.Tag as string == "remove") button.IsEnabled = fileList.SelectedItems.Count > 0;
    }
    private void UpdateFileState()
    {
        if (emptyFiles == null) return;
        var hasFiles = files.Count > 0;
        emptyFiles.Visibility = hasFiles ? Visibility.Collapsed : Visibility.Visible;
        fileList.Visibility = hasFiles ? Visibility.Visible : Visibility.Collapsed;
        fileActions.Visibility = hasFiles ? Visibility.Visible : Visibility.Collapsed;
        fileCount.Text = hasFiles ? $"已添加 {files.Count} 个投影" : "尚未添加文件";
        StartButton.Content = files.Count > 1 ? $"转换 {files.Count} 个投影" : "开始转换";
        StartButton.IsEnabled = !busy && hasFiles && !string.IsNullOrWhiteSpace(output.Text);
        UpdateFileActions();
    }
    private void SelectPreset(string? preset)
    {
        updating = true;
        foreach (var (key, button) in presetButtons) button.IsChecked = key == preset;
        foreach (var (key, tile) in presetTiles)
            tile.Style = (Style)Root.Resources[key == preset ? "PresetTileSelectedStyle" : "PresetTileStyle"];
        updating = false;
        presetLabel.Text = preset switch { "print" => "打印预设", "visual" => "彩色模型预设", "render" => "渲染预设", _ => "自定义配置" };
    }

    private ComboBox Choice(string key, string header, params (string Label, string Value)[] values)
    {
        var combo = new ComboBox { Header = header, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var (label, value) in values) combo.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        combo.SelectedIndex = 0; choices[key] = combo;
        combo.SelectionChanged += (_, _) => Changed();
        return combo;
    }
    private NumberBox Number(string key, string header, double value, double minimum, double maximum)
    {
        var number = new NumberBox { Header = header, Value = value, Minimum = minimum, Maximum = maximum,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact, SmallChange = 0.1 };
        numbers[key] = number; number.ValueChanged += (_, _) => Changed();
        return number;
    }
    private CheckBox Check(string key, string label)
    {
        var check = new CheckBox { Content = label }; checks[key] = check;
        check.Checked += (_, _) => Changed(); check.Unchecked += (_, _) => Changed();
        return check;
    }
    private UIElement BuildAdvanced()
    {
        updating = true;
        var specialOptimization = Check("solid_textures", "**特殊优化：填平贴图镂空**");
        specialOptimization.Foreground = new SolidColorBrush(Microsoft.UI.Colors.IndianRed);
        ToolTipService.SetToolTip(specialOptimization, "仅视觉模式有效。填平叶片、藤蔓等贴图的镂空几何；仍保留贴图透明度。打印模式本来就不裁切透明像素。");
        printOnlyNote = Muted("独立壳体、封闭空腔与并集失败仅在打印模式生效；视觉模式不执行实体布尔处理。", 12);
        printOnlyNote.Visibility = Visibility.Collapsed;
        var basic = Group("模型与输出", "格式、水体与实体处理", Stack(
            Fields(
                Choice("output_format", "格式", ("STL", "stl"), ("OBJ", "obj")),
                Choice("water", "水体处理", ("完整方块", "cube"), ("忽略水体", "drop"), ("水位高度", "level")),
                Choice("fallback", "未知方块", ("回落成立方体", "cube"), ("忽略", "ignore")),
                Choice("geometry", "输出用途", ("打印 · 封闭实体", "print"), ("视觉 · 原版贴图", "visual")),
                Choice("components", "独立壳体", ("全部保留", "keep"), ("删除较小壳体", "remove-small"), ("仅保留主要壳体", "main")),
                Choice("cavities", "封闭空腔", ("保留空腔", "preserve"), ("填充空腔", "fill")),
                Choice("boolean_fallback", "并集失败", ("局部体素 32 回退", "voxel32"), ("失败并停止", "fail"))),
            specialOptimization, printOnlyNote), true);
        printGroup = Group("尺寸与基础选项", "比例、实体厚度与模型居中", Fields(
            Number("scale", "比例", 1, 0.0001, 10000),
            Number("minimum_thickness", "最小实体厚度（格）", 1.0 / 16, 1.0 / 256, 1),
            Number("min_component_volume", "最小壳体体积", 1.0 / 4096, 0, 1e9),
            Check("center", "模型居中")));
        emissionBrowse = Button("选择 JSON", ChooseEmission);
        visualGroup = Group("视觉与发光", "视觉模型带原版贴图，打印模式不带贴图与发光", Stack(
            Check("textures", "是否带有贴图（由输出用途自动决定）"),
            Fields(Choice("blender_lights", "发光模式", ("不发光", "none"), ("仅材质", "material"), ("精确灯光", "exact"), ("聚类灯光", "clustered")),
                Number("emission_strength", "发光强度倍率", 1, 0, 1000)),
            Pair(emissionConfig, emissionBrowse), Check("seamless_glass", "半透明无缝玻璃")));
        var regionGroup = Group("区域", "留空为全部区域；筛选仅用于单个投影", regions);
        // Observe the actual property even while the advanced expander is collapsed.
        regions.RegisterPropertyChangedCallback(TextBox.TextProperty, (_, _) => Changed());
        emissionConfig.RegisterPropertyChangedCallback(TextBox.TextProperty, (_, _) => Changed());
        updating = false;
        return Stack(basic, printGroup, visualGroup, regionGroup);
    }

    private Control BuildActivity()
    {
        emptyResults = Stack(new ContentControl { Content = "cube", ContentTemplate = (DataTemplate)Root.Resources["CubeTemplate"], HorizontalAlignment = HorizontalAlignment.Center },
            Text("模型准备好后，会出现在这里", 20), Muted("转换结果、处理统计和报告会保留在当前会话中。"));
        emptyResults.HorizontalAlignment = HorizontalAlignment.Center;
        emptyResults.Margin = new Thickness(24, 64, 24, 24);
        log.FontFamily = new FontFamily("Cascadia Mono, Consolas");
        log.FontSize = 12; log.MinHeight = 320;
        reportText.FontFamily = new FontFamily("Cascadia Mono, Consolas"); reportText.FontSize = 12; reportText.MinHeight = 320;
        activityTabs = new Pivot { Margin = new Thickness(-12, 0, 0, 0), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        activityTabs.Items.Add(new PivotItem { Header = Text("转换结果", 15), Content = Scroll(Stack(resultCount, emptyResults, resultCards)) });
        activityTabs.Items.Add(new PivotItem { Header = Text("实时日志", 15), Content = Scroll(Stack(Button("清空日志", (_, _) => ClearLog()), log)) });
        reportPrevious = Button("上一页", (_, _) => { reportPage--; ShowReportPage(); });
        reportNext = Button("下一页", (_, _) => { reportPage++; ShowReportPage(); });
        activityTabs.Items.Add(new PivotItem { Header = Text("转换报告", 15),
            Content = Scroll(Stack(Row(reportPrevious, reportNext, reportPageLabel), reportText)) });
        activityTabs.SelectionChanged += (_, _) =>
        {
            if (activityTabs.SelectedIndex == 2 && formattedReport == null)
                _ = PrepareReportAsync(reportRevision);
        };
        UpdateReportPageControls();
        var layout = new Grid { RowSpacing = 12 };
        layout.RowDefinitions.Add(new() { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new() { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        layout.Children.Add(PageHeading("日志与报告", "查看转换进度、输出结果和完整报告。"));
        var toolbar = Row(Button("打开输出文件夹", OpenOutput), Button("导出报告", SaveReport));
        layout.Children.Add(toolbar); Grid.SetRow(toolbar, 1);
        layout.Children.Add(activityTabs); Grid.SetRow(activityTabs, 2);
        return new ContentControl { Content = layout, HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch };
    }
    private void UpdateResults()
    {
        resultCards.Children.Clear();
        emptyResults.Visibility = reports.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        resultCount.Text = reports.Count == 0 ? "" : $"已完成 {reports.Count} 个投影";
        foreach (var report in reports)
        {
            var path = report.GetProperty("output_path").GetString() ?? "";
            var triangles = report.GetProperty("triangles").GetInt64();
            var blocks = report.GetProperty("rendered_blocks").GetInt64();
            var name = Path.GetFileName(path);
            var mode = report.GetProperty("geometry_mode").GetString() == "print" ? "打印模型" : "视觉模型";
            var card = Card(name, mode, Text($"{triangles:N0} 个三角形 · {blocks:N0} 个方块"), Muted(path, 12));
            resultCards.Children.Add(card);
        }
    }
    private Control BuildSettings()
    {
        var theme = new ComboBox { MinWidth = 180, HorizontalAlignment = HorizontalAlignment.Right };
        foreach (var name in new[] { "跟随系统", "浅色", "深色" }) theme.Items.Add(name);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(theme, "外观主题");
        theme.SelectedIndex = (int)Root.RequestedTheme;
        theme.SelectionChanged += (_, _) =>
        {
            Root.RequestedTheme = (ElementTheme)theme.SelectedIndex; settings.Theme = Root.RequestedTheme.ToString(); SaveSettings();
        };
        return Scroll(Stack(PageHeading("偏好设置", "让工作台适合你的使用习惯。"),
            Card("外观", "", Pair(new StackPanel { Spacing = 4, Children = { Text("主题"), Muted("跟随 Windows，或选择固定外观。") } }, theme)),
            Card("转换引擎", "默认自动选择 Python 环境，也可指定解释器。",
                Pair(python, Button("保存设置", (_, _) =>
                { SaveSettings(); ShowNotice("已保存", "应用设置已更新。", InfoBarSeverity.Success); })),
                Muted("留空即可使用内置环境，无需额外配置。", 12)),
            Card("Litematica 3D", $"v{typeof(MainWindow).Assembly.GetName().Version?.ToString(3)} · WinUI 3", Muted("内置 Minecraft 26.2 模型与贴图，无需安装游戏。"),
                Muted("作者：b站@ZZHaccount", 12))));
    }
}
