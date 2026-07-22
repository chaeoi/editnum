from __future__ import annotations

import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import pymupdf
from PIL import ImageTk

from .models import NormalizedRect, PageRecognition, ProcessOptions, build_mapping, matching_key
from .ocr import RapidOcrEngine
from .processor import process_document, recognize_fixed_regions, render_page
from .version import __version__


AUTO_FONT = "自动匹配原字体"


def _windows_font_choices() -> dict[str, str]:
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    choices: dict[str, str] = {}
    key_names = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    for hive, key_name in key_names:
        try:
            key = winreg.OpenKey(hive, key_name)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    label, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(value, str):
                    continue
                path = Path(os.path.expandvars(value))
                if not path.is_absolute():
                    path = font_dir / path
                if path.suffix.lower() not in {".ttf", ".ttc", ".otf", ".otc"} or not path.exists():
                    continue
                clean_label = label.rsplit(" (", 1)[0].strip()
                choices.setdefault(clean_label, str(path))
    return dict(sorted(choices.items(), key=lambda item: item[0].casefold()))


class DrawingNumberApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"PDF 图号替换 {__version__}")
        self.root.geometry("1440x900")
        self.root.minsize(1180, 720)

        self.source_path: Path | None = None
        self.document: pymupdf.Document | None = None
        self.page_index = 0
        self.zoom = 1.0
        self.selection: NormalizedRect | None = None
        self.recognitions: list[PageRecognition] = []
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_size = (1, 1)
        self.drag_start: tuple[float, float] | None = None
        self.selection_item: int | None = None
        self.ocr = RapidOcrEngine()
        self.cancel_event: threading.Event | None = None
        self.busy = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Toolbar.TFrame", padding=(8, 6))
        style.configure("Panel.TFrame", padding=10)
        style.configure("Treeview", rowheight=26)

        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X)
        self.open_button = ttk.Button(toolbar, text="打开 PDF", command=self._open_pdf)
        self.open_button.pack(side=tk.LEFT)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        self.prev_button = ttk.Button(toolbar, text="上一页", command=lambda: self._change_page(-1))
        self.prev_button.pack(side=tk.LEFT)
        self.next_button = ttk.Button(toolbar, text="下一页", command=lambda: self._change_page(1))
        self.next_button.pack(side=tk.LEFT, padx=(4, 0))
        self.page_label = ttk.Label(toolbar, text="0 / 0", width=10, anchor=tk.CENTER)
        self.page_label.pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="缩小", command=lambda: self._change_zoom(0.8)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="放大", command=lambda: self._change_zoom(1.25)).pack(side=tk.LEFT, padx=(4, 0))
        self.zoom_label = ttk.Label(toolbar, text="100%", width=7, anchor=tk.CENTER)
        self.zoom_label.pack(side=tk.LEFT, padx=4)
        self.file_label = ttk.Label(toolbar, text="未打开文件", anchor=tk.W)
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))

        main = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            borderwidth=0,
            sashwidth=5,
            sashrelief=tk.FLAT,
            background=style.lookup("TFrame", "background"),
        )
        main.pack(fill=tk.BOTH, expand=True)

        viewer = ttk.Frame(main)
        main.add(viewer, minsize=540, stretch="always")
        self.canvas = tk.Canvas(viewer, bg="#5a5a5a", highlightthickness=0, cursor="crosshair")
        x_scroll = ttk.Scrollbar(viewer, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(viewer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        viewer.rowconfigure(0, weight=1)
        viewer.columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self._selection_start)
        self.canvas.bind("<B1-Motion>", self._selection_drag)
        self.canvas.bind("<ButtonRelease-1>", self._selection_end)
        self.canvas.bind("<MouseWheel>", self._mousewheel)

        panel = ttk.Frame(main, style="Panel.TFrame", width=520)
        main.add(panel, minsize=520, width=520, stretch="never")

        settings = ttk.LabelFrame(panel, text="替换设置", padding=8)
        settings.pack(fill=tk.X)
        ttk.Label(settings, text="固定前段（可空）").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(settings, text="递增起始（可空）").grid(
            row=0, column=1, sticky=tk.W, padx=(8, 0)
        )
        ttk.Label(settings, text="位数").grid(row=0, column=2, sticky=tk.W, padx=(8, 0))
        ttk.Label(settings, text="固定后段（可空）").grid(
            row=0, column=3, sticky=tk.W, padx=(8, 0)
        )
        self.prefix_var = tk.StringVar(value="00QK0188RJB-LJ-")
        ttk.Entry(settings, textvariable=self.prefix_var, width=20).grid(
            row=1, column=0, sticky=tk.EW, pady=(2, 6)
        )
        self.start_var = tk.StringVar(value="1")
        ttk.Spinbox(settings, from_=0, to=999999, textvariable=self.start_var, width=8).grid(
            row=1, column=1, sticky=tk.EW, padx=(8, 0), pady=(2, 6)
        )
        self.sequence_digits_var = tk.IntVar(value=3)
        ttk.Combobox(
            settings,
            textvariable=self.sequence_digits_var,
            values=(1, 2, 3, 4, 5, 6),
            width=4,
            state="readonly",
        ).grid(row=1, column=2, sticky=tk.EW, padx=(8, 0), pady=(2, 6))
        self.suffix_var = tk.StringVar(value="-0-260715")
        ttk.Entry(settings, textvariable=self.suffix_var, width=14).grid(
            row=1, column=3, sticky=tk.EW, padx=(8, 0), pady=(2, 6)
        )
        self.rule_preview_var = tk.StringVar()
        ttk.Label(settings, textvariable=self.rule_preview_var, anchor=tk.W).grid(
            row=2, column=0, columnspan=4, sticky=tk.EW, pady=(0, 9)
        )

        ttk.Label(settings, text="区域 DPI").grid(row=3, column=0, sticky=tk.W)
        self.fixed_dpi_var = tk.IntVar(value=300)
        ttk.Spinbox(settings, from_=144, to=600, textvariable=self.fixed_dpi_var, width=8).grid(
            row=4, column=0, sticky=tk.W, pady=(2, 7)
        )
        ttk.Label(settings, text="全页 DPI").grid(row=3, column=1, sticky=tk.W, padx=(8, 0))
        self.global_dpi_var = tk.IntVar(value=180)
        ttk.Spinbox(settings, from_=96, to=300, textvariable=self.global_dpi_var, width=8).grid(
            row=4, column=1, sticky=tk.W, padx=(8, 0), pady=(2, 7)
        )
        self.global_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings, text="全页补充搜索替换", variable=self.global_var).grid(
            row=4, column=2, columnspan=2, sticky=tk.W, padx=(8, 0), pady=(2, 7)
        )
        ttk.Label(settings, text="替换字体").grid(row=5, column=0, sticky=tk.W)
        self.font_paths = {AUTO_FONT: "", **_windows_font_choices()}
        self.font_var = tk.StringVar(value=AUTO_FONT)
        self.font_combo = ttk.Combobox(
            settings,
            textvariable=self.font_var,
            values=tuple(self.font_paths),
            state="readonly",
        )
        self.font_combo.grid(row=6, column=0, columnspan=4, sticky=tk.EW, pady=(2, 2))
        settings.columnconfigure(0, weight=3)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(2, weight=1)
        settings.columnconfigure(3, weight=2)

        action_row = ttk.Frame(panel)
        action_row.pack(fill=tk.X, pady=(10, 8))
        self.recognize_button = ttk.Button(action_row, text="识别各页图号", command=self._recognize)
        self.recognize_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.process_button = ttk.Button(action_row, text="执行替换", command=self._process)
        self.process_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        result_frame = ttk.LabelFrame(panel, text="识别与映射", padding=6)
        result_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("page", "source", "target")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("page", text="页")
        self.tree.heading("source", text="原图号")
        self.tree.heading("target", text="新图号")
        self.tree.column("page", width=48, anchor=tk.CENTER, stretch=False)
        self.tree.column("source", width=165, anchor=tk.W)
        self.tree.column("target", width=250, anchor=tk.W)
        tree_scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_x_scroll = ttk.Scrollbar(result_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll.set, xscrollcommand=tree_x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll.grid(row=1, column=0, sticky="ew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._edit_recognition)

        for variable in (
            self.prefix_var,
            self.start_var,
            self.sequence_digits_var,
            self.suffix_var,
        ):
            variable.trace_add("write", self._numbering_rule_changed)
        self._numbering_rule_changed()

        status = ttk.Frame(self.root, padding=(8, 4))
        status.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress = ttk.Progressbar(status, mode="determinate", length=260)
        self.progress.pack(side=tk.LEFT, padx=(8, 6))
        self.cancel_button = ttk.Button(status, text="取消", command=self._cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT)

    def _options(self) -> ProcessOptions:
        start_text = self.start_var.get().strip()
        start_sequence = int(start_text) if start_text else None
        font_name = self.font_var.get()
        options = ProcessOptions(
            fixed_prefix=self.prefix_var.get(),
            fixed_suffix=self.suffix_var.get(),
            start_sequence=start_sequence,
            sequence_digits=int(self.sequence_digits_var.get()),
            fixed_dpi=int(self.fixed_dpi_var.get()),
            global_dpi=int(self.global_dpi_var.get()),
            global_search=bool(self.global_var.get()),
            replacement_font_path=self.font_paths.get(font_name, ""),
            replacement_font_name="" if font_name == AUTO_FONT else font_name,
        )
        options.validate()
        return options

    def _numbering_rule_changed(self, *_args) -> None:
        try:
            options = self._options()
            first_sequence = options.start_sequence or 0
            first = options.number_for(first_sequence)
            second = options.number_for(first_sequence + 1)
        except (ValueError, tk.TclError):
            self.rule_preview_var.set("预览：规则尚未填写完整")
            return
        if not first and not second:
            preview = "（空，将清除图号）"
        else:
            preview = first if first == second else f"{first}  →  {second}"
        self.rule_preview_var.set(f"预览：{preview}")
        if hasattr(self, "tree") and self.recognitions:
            self._refresh_table()

    def _open_pdf(self) -> None:
        filename = filedialog.askopenfilename(filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        if not filename:
            return
        try:
            document = pymupdf.open(filename)
            if len(document) == 0:
                raise ValueError("PDF 没有页面")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)
            return
        if self.document is not None:
            self.document.close()
        self.document = document
        self.source_path = Path(filename)
        self.page_index = 0
        self.selection = None
        self.recognitions.clear()
        self._refresh_table()
        self.file_label.configure(text=self.source_path.name)
        self._render_preview()
        self.status_var.set("PDF 已打开")

    def _render_preview(self) -> None:
        if self.document is None:
            return
        page = self.document[self.page_index]
        image = render_page(page, max(72, int(110 * self.zoom)))
        self.preview_size = image.size
        self.preview_photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.preview_photo, anchor=tk.NW)
        self.canvas.configure(scrollregion=(0, 0, image.width, image.height))
        self.page_label.configure(text=f"{self.page_index + 1} / {len(self.document)}")
        self.zoom_label.configure(text=f"{self.zoom * 100:.0f}%")
        self._draw_selection()
        self._update_navigation()

    def _draw_selection(self) -> None:
        if self.selection is None:
            return
        width, height = self.preview_size
        coords = (
            self.selection.x0 * width,
            self.selection.y0 * height,
            self.selection.x1 * width,
            self.selection.y1 * height,
        )
        self.selection_item = self.canvas.create_rectangle(
            *coords, outline="#d82424", width=2, dash=(6, 3)
        )

    def _change_page(self, delta: int) -> None:
        if self.document is None:
            return
        new_index = min(max(0, self.page_index + delta), len(self.document) - 1)
        if new_index != self.page_index:
            self.page_index = new_index
            self._render_preview()

    def _change_zoom(self, factor: float) -> None:
        if self.document is None:
            return
        self.zoom = min(4.0, max(0.4, self.zoom * factor))
        self._render_preview()

    def _update_navigation(self) -> None:
        count = len(self.document) if self.document is not None else 0
        self.prev_button.configure(state=tk.NORMAL if self.page_index > 0 else tk.DISABLED)
        self.next_button.configure(
            state=tk.NORMAL if count and self.page_index < count - 1 else tk.DISABLED
        )

    def _canvas_point(self, event) -> tuple[float, float]:
        x = min(max(0.0, self.canvas.canvasx(event.x)), float(self.preview_size[0]))
        y = min(max(0.0, self.canvas.canvasy(event.y)), float(self.preview_size[1]))
        return x, y

    def _selection_start(self, event) -> None:
        if self.document is None or self.busy:
            return
        self.drag_start = self._canvas_point(event)
        if self.selection_item is not None:
            self.canvas.delete(self.selection_item)
        x, y = self.drag_start
        self.selection_item = self.canvas.create_rectangle(
            x, y, x, y, outline="#d82424", width=2, dash=(6, 3)
        )

    def _selection_drag(self, event) -> None:
        if self.drag_start is None or self.selection_item is None:
            return
        x, y = self._canvas_point(event)
        self.canvas.coords(self.selection_item, self.drag_start[0], self.drag_start[1], x, y)

    def _selection_end(self, event) -> None:
        if self.drag_start is None:
            return
        x, y = self._canvas_point(event)
        start_x, start_y = self.drag_start
        width, height = self.preview_size
        if abs(x - start_x) < 6 and abs(y - start_y) < 6:
            half_width = width * 0.09
            half_height = height * 0.015
            x0, x1 = max(0, x - half_width), min(width, x + half_width)
            y0, y1 = max(0, y - half_height), min(height, y + half_height)
        else:
            x0, x1 = sorted((start_x, x))
            y0, y1 = sorted((start_y, y))
        self.drag_start = None
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self.selection = NormalizedRect(x0 / width, y0 / height, x1 / width, y1 / height)
        self.recognitions.clear()
        self._refresh_table()
        self._render_preview()
        self.status_var.set("图号区域已选择")

    def _mousewheel(self, event) -> None:
        if event.state & 0x0004:
            self._change_zoom(1.1 if event.delta > 0 else 0.9)
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _recognize(self) -> None:
        if self.source_path is None or self.selection is None:
            messagebox.showwarning("缺少选择", "请打开 PDF 并在图号位置单击或拖框。", parent=self.root)
            return
        try:
            options = self._options()
        except Exception as exc:
            messagebox.showerror("设置错误", str(exc), parent=self.root)
            return
        self._start_worker(
            lambda: recognize_fixed_regions(
                self.source_path,
                self.selection,
                options,
                ocr=self.ocr,
                progress=self._worker_progress,
                cancel_event=self.cancel_event,
            ),
            self._recognition_done,
        )

    def _recognition_done(self, recognitions: list[PageRecognition]) -> None:
        self.recognitions = recognitions
        self._refresh_table()
        self.status_var.set(f"识别完成：共 {len(recognitions)} 页")

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.recognitions:
            return
        try:
            _, mapping = build_mapping(self.recognitions, self._options())
        except Exception:
            mapping = {}
        for item in self.recognitions:
            self.tree.insert(
                "",
                tk.END,
                iid=str(item.page_number),
                values=(
                    item.page_number,
                    item.source,
                    mapping.get(matching_key(item.source), ""),
                ),
            )

    def _edit_recognition(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        page_number = int(row)
        item = self.recognitions[page_number - 1]
        value = simpledialog.askstring(
            "修正识别值",
            f"第 {page_number} 页原图号",
            initialvalue=item.source,
            parent=self.root,
        )
        if value is None or not value.strip():
            return
        item.source = value.strip()
        self._refresh_table()

    def _process(self) -> None:
        if self.source_path is None or self.selection is None or not self.recognitions:
            messagebox.showwarning("尚未识别", "请先选择图号位置并完成识别。", parent=self.root)
            return
        try:
            options = self._options()
        except Exception as exc:
            messagebox.showerror("设置错误", str(exc), parent=self.root)
            return
        default_name = self.source_path.stem + "_图号已修改.pdf"
        filename = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialdir=str(self.source_path.parent),
            initialfile=default_name,
            filetypes=[("PDF 文件", "*.pdf")],
        )
        if not filename:
            return
        output_path = Path(filename)
        self._start_worker(
            lambda: process_document(
                self.source_path,
                output_path,
                self.selection,
                self.recognitions,
                options,
                ocr=self.ocr,
                progress=self._worker_progress,
                cancel_event=self.cancel_event,
            ),
            self._process_done,
        )

    def _process_done(self, report) -> None:
        if self.font_var.get() != AUTO_FONT:
            font_text = f"替换字体：{self.font_var.get()}"
        else:
            font_text = "嵌入字体精确保留" if report.exact_font_preserved else "自动字体匹配，详见日志"
        self.status_var.set("替换完成")
        messagebox.showinfo(
            "处理完成",
            f"固定位置：{report.fixed_replacements}\n"
            f"全局补充：{report.global_replacements}\n"
            f"{font_text}\n\n"
            f"PDF：{report.output_path}\n日志：{report.log_path}",
            parent=self.root,
        )

    def _start_worker(self, operation, on_success) -> None:
        if self.busy:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.cancel_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.DISABLED)
        self.recognize_button.configure(state=tk.DISABLED)
        self.process_button.configure(state=tk.DISABLED)
        self.progress.configure(value=0, maximum=1)

        def run() -> None:
            try:
                result = operation()
            except InterruptedError:
                self.root.after(0, lambda: self._worker_failed("操作已取消", False))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._worker_failed(str(error), True))
            else:
                self.root.after(0, lambda: self._worker_succeeded(result, on_success))

        threading.Thread(target=run, daemon=True).start()

    def _worker_progress(self, current: int, total: int, message: str) -> None:
        self.root.after(0, lambda: self._set_progress(current, total, message))

    def _set_progress(self, current: int, total: int, message: str) -> None:
        self.progress.configure(maximum=max(1, total), value=current)
        self.status_var.set(message)

    def _worker_succeeded(self, result, on_success) -> None:
        self._finish_worker()
        on_success(result)

    def _worker_failed(self, message: str, show_error: bool) -> None:
        self._finish_worker()
        self.status_var.set(message)
        if show_error:
            messagebox.showerror("处理失败", message, parent=self.root)

    def _finish_worker(self) -> None:
        self.busy = False
        self.cancel_event = None
        self.cancel_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.NORMAL)
        self.recognize_button.configure(state=tk.NORMAL)
        self.process_button.configure(state=tk.NORMAL)

    def _cancel(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status_var.set("正在取消")

    def _close(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self.document is not None:
            self.document.close()
        self.root.destroy()


def run() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    DrawingNumberApp(root)
    root.mainloop()
