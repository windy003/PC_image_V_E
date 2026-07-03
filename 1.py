import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QAction, QFileDialog,
                            QLabel, QInputDialog, QMessageBox, QColorDialog, QScrollArea, QPushButton)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QImage, QPen, QCursor, QIcon, QFont
from PyQt5.QtCore import Qt, QPoint, QTemporaryFile, QEvent, QTimer
from PIL import Image, ImageDraw
Image.MAX_IMAGE_PIXELS = None  # 禁用PIL的图片大小限制，允许打开超大图片
import numpy as np
import traceback
import json
import ctypes
import functools
import urllib.parse

VERSION = "2025/11/9-06"

# ============================================================
# 资源管理器排序探测：读取已打开的资源管理器窗口的排序列 + 升降序，
# 让图片左右切换的顺序与资源管理器保持一致。
# 读不到（窗口没开 / 出错）时，回退到文件名自然排序（升序）。
# ============================================================

# StrCmpLogicalW：Windows 资源管理器"名字"列使用的自然排序（10 排在 2 后面）
try:
    _StrCmpLogicalW = ctypes.windll.shlwapi.StrCmpLogicalW
    _StrCmpLogicalW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    _StrCmpLogicalW.restype = ctypes.c_int
except Exception:
    _StrCmpLogicalW = None


def _natural_name_key():
    """返回一个按文件名自然排序的 key 函数（用于 sorted 的 key）。"""
    if _StrCmpLogicalW is not None:
        def _cmp(a, b):
            return _StrCmpLogicalW(os.path.basename(a), os.path.basename(b))
        return functools.cmp_to_key(_cmp)
    # 没有 shlwapi 时退化为普通小写字符串排序
    return lambda p: os.path.basename(p).lower()


# ---- 通过 COM 读取资源管理器当前排序（需要 comtypes，且对应窗口开着）----
try:
    import comtypes
    import comtypes.client
    from comtypes import GUID, COMMETHOD, STDMETHOD, IUnknown, HRESULT
    from ctypes.wintypes import DWORD
    from ctypes import POINTER, c_int, c_uint, byref

    class _GUIDStruct(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    class _PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", _GUIDStruct), ("pid", DWORD)]

    class _SORTCOLUMN(ctypes.Structure):
        _fields_ = [("propkey", _PROPERTYKEY), ("direction", c_int)]

    class _IServiceProvider(IUnknown):
        _iid_ = GUID("{6d5140c1-7436-11ce-8034-00aa006009fa}")
        _methods_ = [
            COMMETHOD([], HRESULT, "QueryService",
                      (['in'], POINTER(GUID), "guidService"),
                      (['in'], POINTER(GUID), "riid"),
                      (['out'], POINTER(ctypes.c_void_p), "ppvObject")),
        ]

    class _IShellView(IUnknown):
        _iid_ = GUID("{000214E3-0000-0000-C000-000000000046}")
        _methods_ = []

    class _IFolderView(IUnknown):
        _iid_ = GUID("{cde725b0-ccc9-4519-917e-325d72fab4ce}")
        _methods_ = [STDMETHOD(HRESULT, n, []) for n in (
            "GetCurrentViewMode", "SetCurrentViewMode", "GetFolder", "Item",
            "ItemCount", "Items", "GetSelectionMarkedItem", "GetFocusedItem",
            "GetItemPosition", "GetSpacing", "GetDefaultSpacing", "GetAutoArrange",
            "SelectItem", "SelectAndPositionItems")]

    class _IFolderView2(_IFolderView):
        _iid_ = GUID("{1af3a467-214f-4298-908e-06b03e0b39f9}")
        _methods_ = [
            STDMETHOD(HRESULT, "SetGroupBy", []),
            STDMETHOD(HRESULT, "GetGroupBy", []),
            STDMETHOD(HRESULT, "SetViewProperty", []),
            STDMETHOD(HRESULT, "GetViewProperty", []),
            STDMETHOD(HRESULT, "SetTileViewProperties", []),
            STDMETHOD(HRESULT, "SetExtendedTileViewProperties", []),
            STDMETHOD(HRESULT, "SetText", []),
            STDMETHOD(HRESULT, "SetCurrentFolderFlags", []),
            STDMETHOD(HRESULT, "GetCurrentFolderFlags", []),
            COMMETHOD([], HRESULT, "GetSortColumnCount",
                      (['out'], POINTER(c_int), "pcColumns")),
            STDMETHOD(HRESULT, "SetSortColumns", []),
            COMMETHOD([], HRESULT, "GetSortColumns",
                      (['in'], POINTER(_SORTCOLUMN), "rgSortColumns"),
                      (['in'], c_uint, "cColumns")),
        ]

    class _IShellBrowser(IUnknown):
        _iid_ = GUID("{000214E2-0000-0000-C000-000000000046}")
        _methods_ = [STDMETHOD(HRESULT, n, []) for n in (
            "GetWindow", "ContextSensitiveHelp", "InsertMenusSB", "SetMenuSB",
            "RemoveMenusSB", "SetStatusTextSB", "EnableModelessSB",
            "TranslateAcceleratorSB", "BrowseObject", "GetViewStateStream",
            "GetControlWindow", "SendControlMsg")] + [
            COMMETHOD([], HRESULT, "QueryActiveShellView",
                      (['out'], POINTER(POINTER(_IShellView)), "ppshv")),
        ]

    _COMTYPES_OK = True
except Exception:
    _COMTYPES_OK = False

# PROPERTYKEY (fmtid, pid) -> 排序类别
_PKEY_FMT = "{B725F130-47EF-101A-A5F1-02608C9EEBAC}"  # System 属性组
_PKEY_TO_KIND = {
    (_PKEY_FMT, 10): "name",
    (_PKEY_FMT, 14): "date_modified",
    (_PKEY_FMT, 12): "size",
    (_PKEY_FMT, 4): "type",
    (_PKEY_FMT, 15): "date_created",
    (_PKEY_FMT, 16): "date_accessed",
}


def _guid_to_str(g):
    return "{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}" % (
        g.Data1, g.Data2, g.Data3,
        g.Data4[0], g.Data4[1], g.Data4[2], g.Data4[3],
        g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7])


def _url_to_path(url):
    """file:// URL -> 本地路径（支持本地盘符与 UNC 网络路径）。"""
    if not url or not url.lower().startswith("file:"):
        return None
    rest = url[len("file:"):]
    if rest.startswith("///"):        # file:///D:/... 本地
        p = urllib.parse.unquote(rest[3:])
        return p.replace("/", "\\")
    if rest.startswith("//"):         # file://host/share/... UNC
        p = urllib.parse.unquote(rest[2:])
        return "\\\\" + p.replace("/", "\\")
    return None


def get_explorer_sort(directory):
    """读取打开着 directory 的资源管理器窗口的排序方式。
    返回 (kind, ascending) 或 None。kind ∈ name/date_modified/size/type/...
    """
    if not _COMTYPES_OK:
        return None
    try:
        target = os.path.normcase(os.path.normpath(directory))
        shell = comtypes.client.CreateObject("Shell.Application")
        windows = shell.Windows()
        for i in range(windows.Count):
            w = windows.Item(i)
            if w is None:
                continue
            try:
                path = _url_to_path(w.LocationURL)
            except Exception:
                continue
            if not path or os.path.normcase(os.path.normpath(path)) != target:
                continue
            sp = w.QueryInterface(_IServiceProvider)
            pbrowser = sp.QueryService(byref(_IShellBrowser._iid_),
                                       byref(_IShellBrowser._iid_))
            browser = ctypes.cast(pbrowser, POINTER(_IShellBrowser))
            shellview = browser.QueryActiveShellView()
            fv2 = shellview.QueryInterface(_IFolderView2)
            n = fv2.GetSortColumnCount()
            if n <= 0:
                return None
            arr = (_SORTCOLUMN * n)()
            fv2.GetSortColumns(arr, n)
            fm = _guid_to_str(arr[0].propkey.fmtid)
            pid = arr[0].propkey.pid
            kind = _PKEY_TO_KIND.get((fm, pid))
            if kind is None:
                return None
            ascending = arr[0].direction >= 0
            return (kind, ascending)
    except Exception:
        return None
    return None


def sort_image_files(all_files, directory):
    """按资源管理器的排序方式给图片文件列表排序；读不到时按文件名自然排序。"""
    sort_info = get_explorer_sort(directory)
    if sort_info is None:
        return sorted(all_files, key=_natural_name_key())

    kind, ascending = sort_info
    if kind == "name":
        result = sorted(all_files, key=_natural_name_key())
    elif kind == "size":
        result = sorted(all_files, key=lambda p: _safe_stat(p, "size"))
    elif kind == "type":
        # 先按扩展名，再按文件名自然排序
        result = sorted(all_files, key=_natural_name_key())
        result = sorted(result, key=lambda p: os.path.splitext(p)[1].lower())
    elif kind in ("date_modified", "date_created", "date_accessed"):
        attr = {"date_modified": "mtime", "date_created": "ctime",
                "date_accessed": "atime"}[kind]
        result = sorted(all_files, key=lambda p: _safe_stat(p, attr))
    else:
        result = sorted(all_files, key=_natural_name_key())

    if not ascending:
        result = list(reversed(result))
    return result


def _safe_stat(path, attr):
    try:
        st = os.stat(path)
        return {"size": st.st_size, "mtime": st.st_mtime,
                "ctime": st.st_ctime, "atime": st.st_atime}[attr]
    except Exception:
        return 0

class DraggableButton(QPushButton):
    """可拖动的按钮类"""
    def __init__(self, text, parent=None, button_id=None):
        super().__init__(text, parent)
        self.dragging = False
        self.drag_position = QPoint()
        self.press_pos = QPoint()
        self.button_id = button_id  # 按钮标识符，用于保存位置

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.press_pos = event.globalPos()
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            # 如果移动距离超过10像素，认为是拖动
            if (event.globalPos() - self.press_pos).manhattanLength() > 10:
                self.dragging = True
                self.move(event.globalPos() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_dragging = self.dragging
            # 如果没有拖动，触发点击事件
            if not self.dragging:
                self.click()
            self.dragging = False

            # 如果进行了拖动，通知父窗口保存位置
            if was_dragging and self.parent():
                if hasattr(self.parent(), 'save_button_positions'):
                    self.parent().save_button_positions()
            event.accept()

class DraggableButtonContainer(QLabel):
    """可拖动的按钮容器，用于将多个按钮组合在一起移动"""
    def __init__(self, parent=None, container_id=None):
        super().__init__(parent)
        self.dragging = False
        self.drag_position = QPoint()
        self.press_pos = QPoint()
        self.container_id = container_id
        self.has_moved = False  # 新增：标记是否有任何移动
        self.setStyleSheet("background: transparent;")

        # 创建手柄区域标签
        self.handle = QLabel(self)
        self.handle.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(100, 100, 100, 200),
                    stop:1 rgba(80, 80, 80, 200));
                border: 2px solid rgba(255, 255, 255, 150);
                border-radius: 15px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
            }
        """)
        self.handle.setFixedHeight(30)
        self.handle.setAlignment(Qt.AlignCenter)

        # 在手柄中央添加三条横线表示可拖动
        handle_icon = QLabel("⋮⋮⋮", self.handle)
        handle_icon.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 200);
                background: transparent;
                border: none;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        handle_icon.setAlignment(Qt.AlignCenter)
        handle_icon.setGeometry(0, 0, 260, 30)

    def is_on_handle(self, pos):
        """检查点击位置是否在手柄区域"""
        handle_rect = self.handle.geometry()
        return handle_rect.contains(pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.has_moved = False  # 重置移动标志
            self.press_pos = event.globalPos()
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()

            # 如果点击在手柄区域，准备拖动整个容器
            if self.is_on_handle(event.pos()):
                event.accept()
            else:
                # 不在手柄区域，让子组件也能接收事件
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            # 检查是否有任何移动（阈值设为3像素，更灵敏）
            if (event.globalPos() - self.press_pos).manhattanLength() > 3:
                self.has_moved = True

            # 如果移动距离超过10像素，认为是拖动
            if (event.globalPos() - self.press_pos).manhattanLength() > 10:
                self.dragging = True
                self.move(event.globalPos() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_dragging = self.dragging

            # 如果有任何移动，阻止点击事件传递给子按钮
            if self.has_moved:
                event.accept()
                # 如果进行了拖动，通知父窗口保存位置
                if was_dragging and self.parent():
                    if hasattr(self.parent(), 'save_button_positions'):
                        self.parent().save_button_positions()
            else:
                # 没有移动，让事件传递给子按钮
                super().mouseReleaseEvent(event)

            # 重置状态
            self.dragging = False
            self.has_moved = False

def resource_path(relative_path):
    """获取资源的绝对路径，兼容开发环境和 PyInstaller 打包后的环境"""
    try:
        # PyInstaller 创建临时文件夹，将路径存储在 _MEIPASS 中
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        # 如果不是打包环境，就使用当前路径
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)

class ImageViewer(QMainWindow):
    def __init__(self, image_path=None):
        super().__init__()
        self.last_save_path = ''  # 添加变量记录上次保存路径
        self.initUI()
        if image_path:
            self.load_image(image_path)

    def initUI(self):
        try:
            # 设置带版本号的窗口标题
            self.setWindowTitle(f'图片查看和编辑工具 v{VERSION}')
            self.setGeometry(100, 100, 800, 600)

            # 设置应用图标
            icon_path = resource_path('1024x1024.png')
            if os.path.exists(icon_path):
                app_icon = QIcon(icon_path)
                self.setWindowIcon(app_icon)
                # 确保应用程序级别的图标也被设置
                QApplication.setWindowIcon(app_icon)
            
            # 创建滚动区域
            self.scroll_area = QScrollArea(self)
            self.scroll_area.setWidgetResizable(True)
            self.setCentralWidget(self.scroll_area)

            # 创建标签用于显示图片
            self.image_label = QLabel()
            self.image_label.setAlignment(Qt.AlignCenter)
            self.scroll_area.setWidget(self.image_label)

            # 设置焦点策略，确保窗口能接收键盘事件
            self.setFocusPolicy(Qt.StrongFocus)

            # 安装事件过滤器，拦截滚动区域的方向键事件
            self.scroll_area.installEventFilter(self)

            # 初始化变量
            self.image = None
            self.drawing = False
            self.last_point = None
            self.brush_size = 20
            self.current_tool = 'draw'  # 'draw', 'blur', 或 'arrow'
            self.brush_color = QColor(255, 0, 0)  # 默认红色 (RGB: 255, 0, 0)
            self.pixmap = None
            self.arrow_start_point = None  # 箭头起点
            self.arrow_end_point = None  # 箭头终点
            self.arrow_width = 5  # 箭头线条宽度
            self.temp_arrow_layer = None  # 临时箭头图层用于预览
            self.scale_factor = 1.0  # 添加缩放因子
            self.min_scale = 0.1  # 最小缩放比例
            self.max_scale = 5.0  # 最大缩放比例
            self.panning = False  # 添加平移状态标志
            self.last_pan_pos = None  # 添加上一次平移位置
            self.grabGesture(Qt.PinchGesture)
            self._pinch_start_scale_factor = 1.0

            # 触摸滑动相关变量
            self.touch_start_pos = None  # 触摸开始位置
            self.touch_current_pos = None  # 当前触摸位置
            self.is_touch_swipe = False  # 是否正在进行触摸滑动
            self.is_touch_panning = False  # 是否正在进行触摸平移
            self.swipe_threshold = 80  # 滑动切换阈值（像素）
            self.is_in_touch_mode = False  # 是否处于触摸模式
            self.touch_point_count = 0  # 当前触摸点数量
            self.is_pinching = False  # 是否正在进行双指缩放
            self.touch_buttons_visible = False  # 触屏按钮是否可见

            # 启用触摸事件
            self.setAttribute(Qt.WA_AcceptTouchEvents, True)

            # 创建菜单栏
            self.create_menus()
            
            # 初始化历史记录
            self.history = []
            self.current_step = -1

            # 创建通知标签
            self.notification_label = QLabel(self)
            self.notification_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(0, 0, 0, 200);
                    color: white;
                    padding: 15px 30px;
                    border-radius: 8px;
                    font-size: 20px;
                    font-weight: bold;
                }
            """)
            self.notification_label.setAlignment(Qt.AlignCenter)
            self.notification_label.hide()

            # 初始化当前图片路径
            self.current_image_path = None

            # 记录最后删除的文件路径，用于撤销删除
            self.last_deleted_file = None

            # 当前目录的图片列表和索引
            self.image_list = []
            self.current_image_index = -1

            # 创建触屏操作按钮
            self.create_touch_buttons()

        except Exception as e:
            QMessageBox.critical(self, '错误', f'初始化失败: {str(e)}')
            print(traceback.format_exc())

    def update_window_title(self):
        """更新窗口标题，包含图片名称和位置信息"""
        try:
            if self.current_image_path:
                image_name = os.path.basename(self.current_image_path)
                # 如果有图片列表，显示位置信息
                if self.image_list and self.current_image_index >= 0:
                    position_info = f"({self.current_image_index + 1}/{len(self.image_list)})"
                    self.setWindowTitle(f'图片查看和编辑工具 v{VERSION} ----------------- {image_name} {position_info}')
                else:
                    self.setWindowTitle(f'图片查看和编辑工具 v{VERSION} ----------------- {image_name}')
            else:
                self.setWindowTitle(f'图片查看和编辑工具 v{VERSION}')
        except Exception as e:
            print(f'更新窗口标题失败: {str(e)}')
            self.setWindowTitle(f'图片查看和编辑工具 v{VERSION}')

    def create_touch_buttons(self):
        """创建触屏操作按钮"""
        try:
            # 创建统一的按钮容器（包含所有五个按钮）
            # 布局：手柄(30px) + 撤销按钮 + 2x2 网格
            #   [手柄区域]
            #      [撤销]
            # [删除]   [上层]
            # [上一张] [下一张]
            self.all_buttons_container = DraggableButtonContainer(self, container_id="all_buttons")
            self.all_buttons_container.setFixedSize(260, 370)  # 30(手柄) + 60(撤销) + 20(间距) + 260(2x2布局)

            # 设置手柄宽度（与容器同宽）
            self.all_buttons_container.handle.setFixedWidth(260)
            self.all_buttons_container.handle.move(0, 0)

            # 创建撤销按钮（手柄下方）
            self.undo_button = QPushButton("↶\n撤销", self.all_buttons_container)
            self.undo_button.setFixedSize(260, 60)
            self.undo_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 149, 0, 220);
                    color: white;
                    border: 4px solid white;
                    border-radius: 30px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 149, 0, 255);
                    border: 5px solid white;
                }
                QPushButton:pressed {
                    background-color: rgba(220, 120, 0, 255);
                    border: 4px solid rgba(255, 255, 255, 180);
                }
            """)
            self.undo_button.clicked.connect(self.handle_undo)
            self.undo_button.move(0, 30)  # 手柄下方

            # 创建删除按钮（不再单独可拖动）
            self.delete_button = QPushButton("🗑️\n删除", self.all_buttons_container)
            self.delete_button.setFixedSize(120, 120)
            self.delete_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 59, 48, 220);
                    color: white;
                    border: 4px solid white;
                    border-radius: 60px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 59, 48, 255);
                    border: 5px solid white;
                }
                QPushButton:pressed {
                    background-color: rgba(200, 40, 30, 255);
                    border: 4px solid rgba(255, 255, 255, 180);
                }
            """)
            self.delete_button.clicked.connect(self.delete_current_image)
            self.delete_button.move(0, 110)  # 左侧，撤销按钮下方

            # 创建移动到上层目录按钮（不再单独可拖动）
            self.move_button = QPushButton("📤\n上层", self.all_buttons_container)
            self.move_button.setFixedSize(120, 120)
            self.move_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(52, 199, 89, 220);
                    color: white;
                    border: 4px solid white;
                    border-radius: 60px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(52, 199, 89, 255);
                    border: 5px solid white;
                }
                QPushButton:pressed {
                    background-color: rgba(40, 160, 70, 255);
                    border: 4px solid rgba(255, 255, 255, 180);
                }
            """)
            self.move_button.clicked.connect(self.copy_to_parent_directory)
            self.move_button.move(140, 110)  # 右侧，撤销按钮下方

            # 创建上一张按钮（不再单独可拖动）
            self.prev_button = QPushButton("◀\n上一张", self.all_buttons_container)
            self.prev_button.setFixedSize(120, 120)
            self.prev_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 122, 255, 220);
                    color: white;
                    border: 4px solid white;
                    border-radius: 60px;
                    font-size: 16px;
                    font-weight: bold;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 122, 255, 255);
                    border: 5px solid white;
                }
                QPushButton:pressed {
                    background-color: rgba(0, 100, 220, 255);
                    border: 4px solid rgba(255, 255, 255, 180);
                }
            """)
            self.prev_button.clicked.connect(self.show_previous_image)
            self.prev_button.move(0, 250)  # 左下角

            # 创建下一张按钮（不再单独可拖动）
            self.next_button = QPushButton("▶\n下一张", self.all_buttons_container)
            self.next_button.setFixedSize(120, 120)
            self.next_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 122, 255, 220);
                    color: white;
                    border: 4px solid white;
                    border-radius: 60px;
                    font-size: 16px;
                    font-weight: bold;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 122, 255, 255);
                    border: 5px solid white;
                }
                QPushButton:pressed {
                    background-color: rgba(0, 100, 220, 255);
                    border: 4px solid rgba(255, 255, 255, 180);
                }
            """)
            self.next_button.clicked.connect(self.show_next_image)
            self.next_button.move(140, 250)  # 右下角

            self.all_buttons_container.hide()

            # 设置初始位置（从配置加载或使用默认位置）
            self.load_button_positions()

        except Exception as e:
            print(f'创建触屏按钮失败: {str(e)}')
            print(traceback.format_exc())

    def get_config_file_path(self):
        """获取配置文件路径"""
        config_dir = os.path.expanduser("~")
        config_file = os.path.join(config_dir, ".image_viewer_config.json")
        return config_file

    def load_button_positions(self):
        """从配置文件加载按钮位置"""
        try:
            config_file = self.get_config_file_path()
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    button_positions = config.get('button_positions', {})

                    # 加载统一按钮容器位置
                    if 'all_buttons' in button_positions:
                        pos = button_positions['all_buttons']
                        self.all_buttons_container.move(pos['x'], pos['y'])
                    else:
                        # 使用默认位置（右下角）
                        self.all_buttons_container.move(self.width() - 280, self.height() - 390)
            else:
                # 配置文件不存在，使用默认位置
                self.all_buttons_container.move(self.width() - 280, self.height() - 390)
        except Exception as e:
            print(f'加载按钮位置失败: {str(e)}')
            # 出错时使用默认位置
            self.all_buttons_container.move(self.width() - 280, self.height() - 390)

    def save_button_positions(self):
        """保存按钮位置到配置文件"""
        try:
            config_file = self.get_config_file_path()

            # 读取现有配置或创建新配置
            config = {}
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)

            # 保存统一按钮容器位置
            button_positions = {}
            button_positions['all_buttons'] = {
                'x': self.all_buttons_container.x(),
                'y': self.all_buttons_container.y()
            }

            config['button_positions'] = button_positions

            # 写入配置文件
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            print(f'按钮位置已保存')
        except Exception as e:
            print(f'保存按钮位置失败: {str(e)}')

    def show_touch_buttons(self):
        """显示触屏按钮"""
        try:
            if self.current_image_path:  # 只有在有图片时才显示
                self.all_buttons_container.show()
                self.all_buttons_container.raise_()
                self.touch_buttons_visible = True
        except Exception as e:
            print(f'显示触屏按钮失败: {str(e)}')

    def hide_touch_buttons(self):
        """隐藏触屏按钮"""
        try:
            self.all_buttons_container.hide()
            self.touch_buttons_visible = False
        except Exception as e:
            print(f'隐藏触屏按钮失败: {str(e)}')

    def toggle_touch_buttons(self):
        """切换触屏按钮的显示/隐藏状态"""
        try:
            if self.touch_buttons_visible:
                self.hide_touch_buttons()
            else:
                self.show_touch_buttons()
        except Exception as e:
            print(f'切换触屏按钮失败: {str(e)}')

    def create_menus(self):
        # 文件菜单
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件(&F)')

        open_action = QAction('打开(&O)', self)
        open_action.setShortcut('Ctrl+O')
        open_action.triggered.connect(self.open_image)
        file_menu.addAction(open_action)

        save_action = QAction('保存(&S)', self)
        save_action.setShortcut('Ctrl+S')
        save_action.triggered.connect(self.save_image)
        file_menu.addAction(save_action)

        # 编辑菜单
        edit_menu = menubar.addMenu('编辑(&E)')

        copy_action = QAction('复制(&C)', self)
        copy_action.setShortcut('Ctrl+C')
        copy_action.triggered.connect(self.copy_image)
        edit_menu.addAction(copy_action)

        paste_action = QAction('粘贴(&V)', self)
        paste_action.setShortcut('Ctrl+V')
        paste_action.triggered.connect(self.paste_image)
        edit_menu.addAction(paste_action)

        undo_action = QAction('撤销(&Z)', self)
        undo_action.setShortcut('Ctrl+Z')
        undo_action.triggered.connect(self.handle_undo)
        edit_menu.addAction(undo_action)

        # 工具菜单
        tool_menu = menubar.addMenu('工具(&T)')

        draw_action = QAction('涂鸦工具(&D)', self)
        draw_action.triggered.connect(lambda: self.set_tool('draw'))
        tool_menu.addAction(draw_action)

        blur_action = QAction('模糊工具(&B)', self)
        blur_action.triggered.connect(lambda: self.set_tool('blur'))
        tool_menu.addAction(blur_action)

        arrow_action = QAction('箭头工具(&A)', self)
        arrow_action.setShortcut('Ctrl+A')
        arrow_action.triggered.connect(lambda: self.set_tool('arrow'))
        tool_menu.addAction(arrow_action)

        # 设置菜单
        settings_menu = menubar.addMenu('设置(&S)')

        brush_size_action = QAction('设置笔刷大小(&B)', self)
        brush_size_action.triggered.connect(self.set_brush_size)
        settings_menu.addAction(brush_size_action)

        color_action = QAction('设置颜色(&C)', self)
        color_action.triggered.connect(self.set_color)
        settings_menu.addAction(color_action)

        # 添加查看菜单
        view_menu = menubar.addMenu('查看(&V)')
        
        zoom_in_action = QAction('放大(&+)', self)
        zoom_in_action.setShortcuts(['Ctrl++', 'Ctrl+='])
        zoom_in_action.triggered.connect(self.zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction('缩小(&-)', self)
        zoom_out_action.setShortcut('Ctrl+-')
        zoom_out_action.triggered.connect(self.zoom_out)
        view_menu.addAction(zoom_out_action)

        reset_zoom_action = QAction('重置缩放(&R)', self)
        reset_zoom_action.setShortcut('Ctrl+0')
        reset_zoom_action.triggered.connect(self.reset_zoom)
        view_menu.addAction(reset_zoom_action)

    def handle_undo(self):
        """统一处理撤销操作"""
        print(f"Debug: handle_undo called, last_deleted_file = {self.last_deleted_file}")
        if self.last_deleted_file:
            print("Debug: Calling undo_delete()")
            self.undo_delete()
        else:
            print("Debug: Calling undo()")
            self.undo()

    def show_notification(self, message, duration=1500):
        """显示一个临时通知，自动消失"""
        self.notification_label.setText(message)

        # 调整通知标签大小和位置
        self.notification_label.adjustSize()
        label_width = self.notification_label.width()
        label_height = self.notification_label.height()
        x = (self.width() - label_width) // 2
        y = 50  # 距离顶部50像素
        self.notification_label.setGeometry(x, y, label_width, label_height)

        # 显示通知
        self.notification_label.show()
        self.notification_label.raise_()

        # 设置定时器自动隐藏
        QTimer.singleShot(duration, self.notification_label.hide)

    def delete_current_image(self):
        """删除当前显示的图片文件（移动到回收站）"""
        try:
            if not self.current_image_path:
                self.show_notification("没有可删除的图片")
                return

            # 先更新图片列表，确保列表是最新的
            self.update_image_list()

            if not os.path.exists(self.current_image_path):
                self.show_notification("图片文件不存在")
                return

            # 记录删除的文件路径和索引
            deleted_path = self.current_image_path
            filename = os.path.basename(deleted_path)

            # 记录当前图片在列表中的索引（删除前）
            if deleted_path in self.image_list:
                deleted_index = self.image_list.index(deleted_path)
            else:
                deleted_index = self.current_image_index

            # 创建临时备份文件（用于撤销）
            import tempfile
            import shutil
            import time
            temp_dir = tempfile.gettempdir()
            backup_path = os.path.join(temp_dir, f"image_backup_{filename}")

            # 备份文件
            shutil.copy2(deleted_path, backup_path)

            # 使用 send2trash 库移动到回收站（无确认框）
            try:
                from send2trash import send2trash
                send2trash(deleted_path)
                success = True
            except ImportError:
                # 如果没有 send2trash 库，使用 win32api
                import win32api
                import win32con
                # FOF_ALLOWUNDO: 允许撤销（移到回收站）
                # FOF_NOCONFIRMATION: 不显示确认对话框
                # FOF_SILENT: 静默操作
                result = win32api.SHFileOperation((
                    0,  # hwnd
                    win32con.FO_DELETE,  # 操作类型：删除
                    deleted_path,  # 源文件
                    None,  # 目标（删除操作不需要）
                    win32con.FOF_ALLOWUNDO | win32con.FOF_NOCONFIRMATION | win32con.FOF_SILENT,  # 标志
                    None,  # 进度标题
                    None   # 进度文本
                ))
                success = (result == 0)

            if success:

                # 记录最后删除的文件，用于撤销
                self.last_deleted_file = {
                    'path': deleted_path,
                    'filename': filename,
                    'directory': os.path.dirname(deleted_path),
                    'backup_path': backup_path
                }

                print(f"Debug: File deleted, last_deleted_file set to: {self.last_deleted_file}")

                # 等待文件系统完成删除操作
                time.sleep(0.2)

                # 重新扫描目录获取最新的图片列表
                directory = os.path.dirname(deleted_path)
                image_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')
                all_files = []
                for file in os.listdir(directory):
                    if file.lower().endswith(image_extensions):
                        full_path = os.path.normpath(os.path.join(directory, file))
                        # 确保文件真实存在且可访问
                        if os.path.exists(full_path) and os.path.isfile(full_path):
                            all_files.append(full_path)
                self.image_list = sort_image_files(all_files, directory)
                self._listed_dir = directory

                # 根据删除前的索引，加载下一张图片
                if self.image_list:
                    # 如果删除的是最后一张，则显示新的最后一张
                    if deleted_index >= len(self.image_list):
                        self.current_image_index = len(self.image_list) - 1
                    else:
                        # 否则显示相同索引位置的图片（原来的下一张）
                        self.current_image_index = deleted_index

                    # 加载图片
                    next_image_path = self.image_list[self.current_image_index]

                    # 直接加载图片，不调用 load_image 以避免再次更新列表
                    self.image = Image.open(next_image_path)
                    self.current_image_path = next_image_path
                    self.last_save_path = next_image_path
                    self.add_to_history()
                    self.display_image()

                    # 更新窗口标题
                    next_filename = os.path.basename(next_image_path)
                    self.update_window_title()

                    # 显示通知
                    self.show_notification(f"已删除 {filename}，切换到: {next_filename} ({self.current_image_index + 1}/{len(self.image_list)})")
                else:
                    # 如果没有图片了，清空显示
                    self.image = None
                    self.current_image_path = None
                    self.image_label.clear()
                    self.current_image_index = -1
                    self.show_notification(f"已删除: {filename} (Ctrl+Z 可撤销)")
            else:
                self.show_notification("无法访问该文件")
                # 删除失败，清理备份文件
                if os.path.exists(backup_path):
                    os.remove(backup_path)

        except Exception as e:
            self.show_notification(f"删除失败: {str(e)}")
            print(traceback.format_exc())

    def undo_delete(self):
        """撤销删除操作（从备份恢复）"""
        try:
            print(f"Debug: undo_delete called, last_deleted_file = {self.last_deleted_file}")

            if not self.last_deleted_file:
                self.show_notification("没有可撤销的删除操作")
                return

            deleted_info = self.last_deleted_file
            deleted_path = deleted_info['path']
            filename = deleted_info['filename']
            backup_path = deleted_info['backup_path']

            print(f"Debug: Attempting to restore from {backup_path} to {deleted_path}")

            # 检查备份文件是否存在
            if not os.path.exists(backup_path):
                self.show_notification("备份文件不存在，无法恢复")
                self.last_deleted_file = None
                return

            # 从备份恢复文件
            import shutil
            shutil.copy2(backup_path, deleted_path)
            print(f"Debug: File restored successfully")

            # 删除备份文件
            os.remove(backup_path)

            self.show_notification(f"已恢复: {filename}")

            # 重新加载图片
            self.load_image(deleted_path)

            # 清除删除记录
            self.last_deleted_file = None

        except Exception as e:
            self.show_notification(f"撤销失败: {str(e)}")
            print(traceback.format_exc())

    def update_image_list(self):
        """更新当前目录的图片列表"""
        try:
            if not self.current_image_path:
                self.image_list = []
                self.current_image_index = -1
                return

            # 规范化当前图片路径
            current_normalized = os.path.normpath(os.path.abspath(self.current_image_path))

            # 获取当前图片所在目录
            directory = os.path.dirname(current_normalized)

            # 快路径：同一目录内切换图片时，列表和排序不会变，
            # 直接复用缓存、只更新索引，避免每次都重扫目录 + 跑 COM 探测（这是卡顿的主因）。
            if (getattr(self, '_listed_dir', None) == directory
                    and self.image_list and current_normalized in self.image_list):
                self.current_image_index = self.image_list.index(current_normalized)
                return

            # 支持的图片格式
            image_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')

            # 获取目录中所有图片文件
            all_files = []
            for file in os.listdir(directory):
                if file.lower().endswith(image_extensions):
                    full_path = os.path.normpath(os.path.join(directory, file))
                    # 确保文件真实存在且可访问
                    if os.path.exists(full_path) and os.path.isfile(full_path):
                        all_files.append(full_path)

            # 按资源管理器的排序方式排序（读不到则按文件名自然排序）
            self.image_list = sort_image_files(all_files, directory)
            self._listed_dir = directory

            # 找到当前图片的索引
            try:
                self.current_image_index = self.image_list.index(current_normalized)
            except ValueError:
                # 如果找不到，尝试比较文件名
                current_filename = os.path.basename(current_normalized)
                for i, path in enumerate(self.image_list):
                    if os.path.basename(path) == current_filename:
                        self.current_image_index = i
                        break
                else:
                    self.current_image_index = -1

        except Exception as e:
            print(f"Error updating image list: {str(e)}")
            print(traceback.format_exc())
            self.image_list = []
            self.current_image_index = -1

    def show_previous_image(self):
        """显示上一张图片"""
        try:
            if not self.image_list:
                self.update_image_list()

            if not self.image_list:
                self.show_notification("当前目录没有其他图片")
                return

            if self.current_image_index <= 0:
                self.show_notification("已经是第一张图片")
                return

            # 加载上一张图片
            self.current_image_index -= 1
            next_image_path = self.image_list[self.current_image_index]
            self.load_image(next_image_path)

            # 显示通知
            filename = os.path.basename(next_image_path)
            self.show_notification(f"← {filename} ({self.current_image_index + 1}/{len(self.image_list)})")

        except Exception as e:
            self.show_notification(f"切换图片失败: {str(e)}")
            print(traceback.format_exc())

    def show_next_image(self):
        """显示下一张图片"""
        try:
            if not self.image_list:
                self.update_image_list()

            if not self.image_list:
                self.show_notification("当前目录没有其他图片")
                return

            if self.current_image_index >= len(self.image_list) - 1:
                self.show_notification("已经是最后一张图片")
                return

            # 加载下一张图片
            self.current_image_index += 1
            next_image_path = self.image_list[self.current_image_index]
            self.load_image(next_image_path)

            # 显示通知
            filename = os.path.basename(next_image_path)
            self.show_notification(f"→ {filename} ({self.current_image_index + 1}/{len(self.image_list)})")

        except Exception as e:
            self.show_notification(f"切换图片失败: {str(e)}")
            print(traceback.format_exc())

    def copy_to_parent_directory(self):
        """将当前图片复制到上层目录，然后删除当前图片并加载下一张"""
        try:
            if not self.current_image_path:
                self.show_notification("没有可复制的图片")
                return

            if not os.path.exists(self.current_image_path):
                self.show_notification("图片文件不存在")
                return

            # 获取当前文件的目录和文件名
            current_dir = os.path.dirname(self.current_image_path)
            filename = os.path.basename(self.current_image_path)

            # 获取上层目录
            parent_dir = os.path.dirname(current_dir)

            # 目标路径
            destination = os.path.join(parent_dir, filename)

            # 如果目标文件已存在，添加编号
            if os.path.exists(destination):
                name, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(destination):
                    new_filename = f"{name}_{counter}{ext}"
                    destination = os.path.join(parent_dir, new_filename)
                    counter += 1

            # 复制文件
            import shutil
            shutil.copy2(self.current_image_path, destination)

            # 复制成功后，删除当前图片（会自动加载下一张）
            copied_filename = os.path.basename(destination)
            self.delete_current_image()

            # 显示通知（覆盖删除操作的通知）
            self.show_notification(f"已复制到上层: {copied_filename}")

        except Exception as e:
            self.show_notification(f"操作失败: {str(e)}")
            print(traceback.format_exc())

    def eventFilter(self, obj, event):
        """事件过滤器，拦截滚动区域的方向键事件"""
        if obj == self.scroll_area and event.type() == QEvent.KeyPress:
            key = event.key()
            # 如果是方向键，转发到主窗口处理
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                print(f"Debug: Arrow key intercepted by event filter: {key}")
                self.keyPressEvent(event)
                return True  # 阻止事件继续传播
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        key = event.key()
        print(f"Debug: Key pressed: {key}")
        print(f"Debug: Qt.Key_Left = {Qt.Key_Left}, Qt.Key_Right = {Qt.Key_Right}")
        print(f"Debug: Qt.Key_Up = {Qt.Key_Up}, Qt.Key_Down = {Qt.Key_Down}")

        if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            self.paste_image()
        elif event.key() == Qt.Key_Z and event.modifiers() == Qt.ControlModifier:
            self.handle_undo()
        elif event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self.copy_image()
        elif event.key() == Qt.Key_Delete:
            self.delete_current_image()
        elif event.key() == Qt.Key_M and event.modifiers() == Qt.ControlModifier:
            self.copy_to_parent_directory()
        elif event.key() in (Qt.Key_Left, Qt.Key_Up):
            print("Debug: Left/Up arrow key detected, calling show_previous_image()")
            self.show_previous_image()
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            print("Debug: Right/Down arrow key detected, calling show_next_image()")
            self.show_next_image()
        else:
            super().keyPressEvent(event)

    def get_image_coordinates(self, pos):
        """将窗口坐标转换为图像坐标"""
        try:
            if not self.image or not self.image_label.pixmap():
                return None, None

            # 获取图像标签的几何信息
            label_rect = self.image_label.geometry()
            pixmap = self.image_label.pixmap()
            
            # 计算图像在标签中的实际显示区域
            scaled_size = pixmap.size()
            scaled_size.scale(label_rect.size(), Qt.KeepAspectRatio)
            
            # 计算图像的偏移量（居中显示）
            x_offset = (label_rect.width() - scaled_size.width()) / 2
            y_offset = (label_rect.height() - scaled_size.height()) / 2
            
            # 将窗口坐标转换为图像坐标，考虑缩放因子
            image_x = (pos.x() - x_offset) * self.image.width / (scaled_size.width() * self.scale_factor)
            image_y = (pos.y() - y_offset) * self.image.height / (scaled_size.height() * self.scale_factor)
            
            # 确保坐标在图像范围内
            image_x = max(0, min(image_x, self.image.width - 1))
            image_y = max(0, min(image_y, self.image.height - 1))
            
            return int(image_x), int(image_y)
        except Exception as e:
            print(f"坐标转换错误: {str(e)}")
            return None, None

    def apply_blur_at_point(self, x, y):
        try:
            if not self.image:
                return

            # 确保图像是RGBA模式
            if self.image.mode != 'RGBA':
                self.image = self.image.convert('RGBA')

            # 获取笔刷范围
            left = max(0, x - self.brush_size)
            top = max(0, y - self.brush_size)
            right = min(self.image.width, x + self.brush_size)
            bottom = min(self.image.height, y + self.brush_size)

            # 确保区域有效
            if right <= left or bottom <= top:
                return

            # 提取区域并应用模糊效果
            region = self.image.crop((left, top, right, bottom))
            if region.size[0] > 0 and region.size[1] > 0:
                # 确保区域也是RGBA模式
                if region.mode != 'RGBA':
                    region = region.convert('RGBA')
                blurred = region.resize((max(1, (right-left)//4), max(1, (bottom-top)//4))).resize((right-left, bottom-top))
                self.image.paste(blurred, (left, top))
        except Exception as e:
            QMessageBox.critical(self, '错误', f'应用模糊效果失败: {str(e)}')
            print(traceback.format_exc())

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton and self.image:
                # 如果处于触摸模式，不触发涂鸦，完全禁用
                if self.is_in_touch_mode:
                    return

                if event.modifiers() == Qt.AltModifier:  # 按住Alt键进行平移
                    self.panning = True
                    self.last_pan_pos = event.pos()
                    self.setCursor(Qt.ClosedHandCursor)
                else:  # 正常的绘画操作
                    self.drawing = True
                    self.add_to_history()
                    pos = self.image_label.mapFrom(self, event.pos())

                    if self.current_tool == 'arrow':
                        # 箭头工具：记录起点
                        self.arrow_start_point = pos
                        self.arrow_end_point = pos
                        # 保存当前图像用于预览
                        self.temp_arrow_layer = self.image.copy()
                    else:
                        self.last_point = pos
                        self.apply_effect(pos)
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标按下事件失败: {str(e)}')
            print(traceback.format_exc())

    def mouseMoveEvent(self, event):
        try:
            # 如果处于触摸模式，不触发涂鸦
            if self.is_in_touch_mode:
                return

            if self.panning and self.last_pan_pos:
                # 计算移动距离
                delta = event.pos() - self.last_pan_pos
                # 更新滚动条位置
                self.scroll_area.horizontalScrollBar().setValue(
                    self.scroll_area.horizontalScrollBar().value() - delta.x())
                self.scroll_area.verticalScrollBar().setValue(
                    self.scroll_area.verticalScrollBar().value() - delta.y())
                self.last_pan_pos = event.pos()
            elif self.drawing and self.image:
                pos = self.image_label.mapFrom(self, event.pos())

                if self.current_tool == 'arrow' and self.arrow_start_point:
                    # 箭头工具：更新终点并显示预览
                    self.arrow_end_point = pos
                    # 恢复原始图像
                    self.image = self.temp_arrow_layer.copy()
                    # 绘制预览箭头
                    start_x, start_y = self.get_image_coordinates(self.arrow_start_point)
                    end_x, end_y = self.get_image_coordinates(self.arrow_end_point)
                    if start_x is not None and end_x is not None:
                        draw = ImageDraw.Draw(self.image)
                        self.draw_arrow(draw, start_x, start_y, end_x, end_y,
                                      self.brush_color.getRgb()[:3], self.arrow_width)
                    self.display_image()
                else:
                    self.apply_effect(pos)
                    self.last_point = pos
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标移动事件失败: {str(e)}')
            print(traceback.format_exc())

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                # 如果处于触摸模式，不触发涂鸦
                if self.is_in_touch_mode:
                    return

                if self.panning:
                    self.panning = False
                    self.last_pan_pos = None
                    self.setCursor(Qt.ArrowCursor)
                else:
                    if self.current_tool == 'arrow' and self.arrow_start_point and self.arrow_end_point:
                        # 箭头工具：完成绘制
                        # 恢复原始图像
                        self.image = self.temp_arrow_layer.copy()
                        # 绘制最终箭头
                        start_x, start_y = self.get_image_coordinates(self.arrow_start_point)
                        end_x, end_y = self.get_image_coordinates(self.arrow_end_point)
                        if start_x is not None and end_x is not None:
                            draw = ImageDraw.Draw(self.image)
                            self.draw_arrow(draw, start_x, start_y, end_x, end_y,
                                          self.brush_color.getRgb()[:3], self.arrow_width)
                        # 清除箭头状态
                        self.arrow_start_point = None
                        self.arrow_end_point = None
                        self.temp_arrow_layer = None

                    self.drawing = False
                    self.display_image()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标释放事件失败: {str(e)}')
            print(traceback.format_exc())

    def apply_effect(self, pos):
        try:
            if not self.image:
                return

            # 获取图像坐标
            x, y = self.get_image_coordinates(pos)
            if x is None or y is None:
                return

            # 确保图像是RGBA模式
            if self.image.mode != 'RGBA':
                self.image = self.image.convert('RGBA')

            if self.current_tool == 'blur':
                self.apply_blur_at_point(x, y)
            else:  # draw
                draw = ImageDraw.Draw(self.image)
                if self.last_point:
                    last_x, last_y = self.get_image_coordinates(self.last_point)
                    if last_x is not None and last_y is not None:
                        draw.line([(last_x, last_y), (x, y)], 
                                fill=self.brush_color.getRgb()[:3], 
                                width=self.brush_size)

            self.display_image()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'应用效果失败: {str(e)}')
            print(traceback.format_exc())

    def draw_arrow(self, draw_obj, start_x, start_y, end_x, end_y, color, width):
        """绘制箭头的辅助方法"""
        import math

        # 绘制箭头主线
        draw_obj.line([(start_x, start_y), (end_x, end_y)], fill=color, width=width)

        # 计算箭头方向
        dx = end_x - start_x
        dy = end_y - start_y
        length = math.sqrt(dx**2 + dy**2)

        if length == 0:
            return

        # 标准化方向向量
        dx = dx / length
        dy = dy / length

        # 箭头头部大小（根据线条宽度调整）
        arrow_head_length = max(width * 4, 20)
        arrow_head_width = max(width * 2.5, 12)

        # 计算箭头头部的三个点
        # 箭头尖端就是终点
        tip_x, tip_y = end_x, end_y

        # 计算箭头两侧的点
        # 沿着箭头方向往回一段距离
        back_x = end_x - dx * arrow_head_length
        back_y = end_y - dy * arrow_head_length

        # 垂直方向的向量
        perp_x = -dy
        perp_y = dx

        # 箭头两侧的点
        left_x = back_x + perp_x * arrow_head_width
        left_y = back_y + perp_y * arrow_head_width
        right_x = back_x - perp_x * arrow_head_width
        right_y = back_y - perp_y * arrow_head_width

        # 绘制箭头头部（三角形）
        draw_obj.polygon([(tip_x, tip_y), (left_x, left_y), (right_x, right_y)], fill=color)

    def set_tool(self, tool):
        self.current_tool = tool
        if tool == 'draw':
            QMessageBox.information(self, '工具切换', '已切换到涂鸦工具')
        elif tool == 'blur':
            QMessageBox.information(self, '工具切换', '已切换到模糊工具')
        else:
            QMessageBox.information(self, '工具切换', '已切换到箭头工具\n\n使用方法：\n1. 点击设置箭头起点\n2. 拖动到终点位置\n3. 释放鼠标完成绘制')

    def set_brush_size(self):
        size, ok = QInputDialog.getInt(self, '设置笔刷大小', 
                                     '请输入笔刷大小 (1-100):', 
                                     self.brush_size, 1, 100)
        if ok:
            self.brush_size = size

    def set_color(self):
        color = QColorDialog.getColor(self.brush_color, self, '选择颜色')
        if color.isValid():
            self.brush_color = color

    def open_image(self):
        try:
            # 使用上次的保存路径作为打开对话框的默认路径
            initial_path = self.last_save_path if self.last_save_path else ''
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                '打开图片',
                initial_path,  # 使用记住的路径
                'Images (*.png *.jpg *.jpeg *.bmp)'
            )

            if file_path:
                self.image = Image.open(file_path)
                self.last_save_path = file_path  # 同时更新保存路径
                self.current_image_path = file_path  # 设置当前图片路径
                self.add_to_history()
                self.display_image()

                # 更新图片列表
                self.update_image_list()

                # 更新窗口标题显示图片名称
                self.update_window_title()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'打开图片失败: {str(e)}')
            print(traceback.format_exc())

    def save_image(self):
        if self.image:
            try:
                # 获取桌面路径
                import os
                desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
                
                # 如果有上次保存路径，优先使用上次路径
                initial_path = self.last_save_path if hasattr(self, 'last_save_path') and self.last_save_path else os.path.join(desktop_path, "未命名.png")
                
                file_path, _ = QFileDialog.getSaveFileName(
                    self, 
                    '保存图片', 
                    initial_path,
                    'Images (*.png *.jpg *.jpeg *.bmp)'
                )
                
                if file_path:
                    # 保存图像
                    self.image.save(file_path)
                    # 记住这次的保存路径，以便下次使用
                    self.last_save_path = file_path
                    QMessageBox.information(self, '提示', '图片保存成功')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存图片失败: {str(e)}')
                import traceback
                print(traceback.format_exc())

    def display_image(self):
        try:
            if self.image:
                # 将PIL Image转换为QPixmap
                data = self.image.convert("RGBA").tobytes("raw", "RGBA")
                qim = QImage(data, self.image.width, self.image.height, QImage.Format_RGBA8888)
                self.pixmap = QPixmap.fromImage(qim)
                
                # 计算缩放后的大小
                scaled_width = int(self.pixmap.width() * self.scale_factor)
                scaled_height = int(self.pixmap.height() * self.scale_factor)
                
                # 应用缩放
                scaled_pixmap = self.pixmap.scaled(scaled_width, scaled_height, 
                                                 Qt.KeepAspectRatio, 
                                                 Qt.SmoothTransformation)
                self.image_label.setPixmap(scaled_pixmap)
                
                # 调整标签大小以适应缩放后的图片
                self.image_label.resize(scaled_pixmap.size())
        except Exception as e:
            QMessageBox.critical(self, '错误', f'显示图片失败: {str(e)}')
            print(traceback.format_exc())

    def add_to_history(self):
        if self.image:
            try:
                # 确保添加到历史记录的是一个新的副本
                self.current_step += 1
                if self.current_step < len(self.history):
                    self.history = self.history[:self.current_step]
                # 确保复制的图像是RGBA模式
                image_copy = self.image.copy()
                if image_copy.mode != 'RGBA':
                    image_copy = image_copy.convert('RGBA')
                self.history.append(image_copy)
            except Exception as e:
                QMessageBox.critical(self, '错误', f'添加历史记录失败: {str(e)}')
                print(traceback.format_exc())

    def undo(self):
        if self.current_step > 0:
            self.current_step -= 1
            self.image = self.history[self.current_step].copy()
            self.display_image()

    def paste_image(self):
        try:
            clipboard = QApplication.clipboard()
            mime_data = clipboard.mimeData()
            
            if mime_data.hasImage():
                # 从剪贴板获取QImage
                q_image = clipboard.image()
                
                if q_image.isNull():
                    QMessageBox.warning(self, "警告", "剪贴板中的图像无效")
                    return
                
                # 使用更可靠的方法转换QImage到PIL Image
                q_image = q_image.convertToFormat(QImage.Format_RGBA8888)
                width, height = q_image.width(), q_image.height()
                
                # 获取图像数据
                bits = q_image.constBits()
                bits.setsize(q_image.byteCount())
                
                # 创建PIL图像
                buffer = bytes(bits)
                self.image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "RGBA", 0, 1)
                
                # 重置缩放和历史
                self.scale_factor = 1.0
                self.history = []
                self.history_index = -1
                self.add_to_history()
                
                # 显示图像
                self.display_image()
            else:
                QMessageBox.information(self, "提示", "剪贴板中没有图像")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"粘贴图像时出错: {str(e)}")
            import traceback
            print(traceback.format_exc())

    def copy_image(self):
        try:
            if self.image:
                # 将PIL Image转换为QImage
                data = self.image.convert("RGBA").tobytes("raw", "RGBA")
                qimage = QImage(data, self.image.width, self.image.height, QImage.Format_RGBA8888)
                
                # 将QImage设置到剪贴板
                clipboard = QApplication.clipboard()
                clipboard.setImage(qimage)
                QMessageBox.information(self, '提示', '图片已复制到剪贴板')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'复制图片失败: {str(e)}')
            print(traceback.format_exc())

    def event(self, event):
        if event.type() == QEvent.Gesture:
            return self.gestureEvent(event)
        elif event.type() == QEvent.TouchBegin:
            return self.touchBeginEvent(event)
        elif event.type() == QEvent.TouchUpdate:
            return self.touchUpdateEvent(event)
        elif event.type() == QEvent.TouchEnd:
            return self.touchEndEvent(event)
        return super(ImageViewer, self).event(event)

    def gestureEvent(self, event):
        pinch = event.gesture(Qt.PinchGesture)
        if pinch:
            if pinch.state() == Qt.GestureStarted:
                self._pinch_start_scale_factor = self.scale_factor
                self.is_pinching = True
                self.is_in_touch_mode = True  # 进入触摸模式
            elif pinch.state() == Qt.GestureUpdated:
                self.is_pinching = True
                new_scale = self._pinch_start_scale_factor * pinch.totalScaleFactor()
                if self.min_scale <= new_scale <= self.max_scale:
                    # 获取手势中心点
                    center_point = pinch.centerPoint().toPoint()
                    # 转换为相对于 image_label 的坐标
                    label_pos = self.image_label.mapFromGlobal(self.mapToGlobal(center_point))

                    # 获取滚动条的当前位置
                    h_bar = self.scroll_area.horizontalScrollBar()
                    v_bar = self.scroll_area.verticalScrollBar()
                    h_offset = h_bar.value()
                    v_offset = v_bar.value()

                    # 计算缩放前的鼠标在完整图片中的位置
                    before_x = (h_offset + label_pos.x()) / self.scale_factor
                    before_y = (v_offset + label_pos.y()) / self.scale_factor

                    # 更新缩放因子
                    self.scale_factor = new_scale
                    self.display_image()

                    # 计算缩放后的鼠标在完整图片中的位置
                    after_x = before_x * self.scale_factor
                    after_y = before_y * self.scale_factor

                    # 计算新的滚动条位置，以保持鼠标下的点不变
                    new_h_offset = after_x - label_pos.x()
                    new_v_offset = after_y - label_pos.y()

                    # 设置新的滚动条位置
                    h_bar.setValue(int(new_h_offset))
                    v_bar.setValue(int(new_v_offset))
            elif pinch.state() == Qt.GestureFinished or pinch.state() == Qt.GestureCanceled:
                self.is_pinching = False
                # 延迟退出触摸模式
                QTimer.singleShot(100, self.exit_touch_mode)

            return True
        return False

    def touchBeginEvent(self, event):
        """处理触摸开始事件"""
        try:
            touch_points = event.touchPoints()
            self.touch_point_count = len(touch_points)

            # 进入触摸模式
            self.is_in_touch_mode = True

            if len(touch_points) == 1:  # 单指触摸
                point = touch_points[0]
                self.touch_start_pos = point.pos()
                self.touch_current_pos = point.pos()
                self.is_touch_swipe = False
                self.is_touch_panning = False

                event.accept()
                return True
        except Exception as e:
            print(f'触摸开始事件失败: {str(e)}')
        return False

    def touchUpdateEvent(self, event):
        """处理触摸更新事件"""
        try:
            touch_points = event.touchPoints()
            self.touch_point_count = len(touch_points)

            # 如果正在双指缩放，不处理单指平移
            if self.is_pinching or len(touch_points) > 1:
                return True

            if len(touch_points) == 1 and self.touch_start_pos:  # 单指操作
                point = touch_points[0]
                prev_pos = self.touch_current_pos if self.touch_current_pos else self.touch_start_pos
                self.touch_current_pos = point.pos()

                # 计算从起始点的总距离
                dx_total = self.touch_current_pos.x() - self.touch_start_pos.x()
                dy_total = self.touch_current_pos.y() - self.touch_start_pos.y()

                # 计算本次移动的增量
                dx_delta = self.touch_current_pos.x() - prev_pos.x()
                dy_delta = self.touch_current_pos.y() - prev_pos.y()

                # 判断是否应该进行平移
                # 如果还没有确定操作类型，先判断用户意图
                if not self.is_touch_swipe and not self.is_touch_panning:
                    # 移动距离足够大才判断意图
                    if abs(dx_total) > 15 or abs(dy_total) > 15:
                        # 如果主要是水平移动，标记为可能的滑动
                        if abs(dx_total) > abs(dy_total) * 1.5:
                            # 暂时不确定，继续观察
                            pass
                        else:
                            # 主要是垂直或斜向移动，确定为平移
                            self.is_touch_panning = True

                # 如果已确定为平移，或者用户正在移动
                if self.is_touch_panning or (abs(dx_delta) > 0 or abs(dy_delta) > 0):
                    if not self.is_touch_swipe:  # 如果不是滑动模式，就进行平移
                        self.is_touch_panning = True
                        # 更新滚动条位置（平移）
                        h_bar = self.scroll_area.horizontalScrollBar()
                        v_bar = self.scroll_area.verticalScrollBar()
                        h_bar.setValue(int(h_bar.value() - dx_delta))
                        v_bar.setValue(int(v_bar.value() - dy_delta))

                event.accept()
                return True
        except Exception as e:
            print(f'触摸更新事件失败: {str(e)}')
        return False

    def touchEndEvent(self, event):
        """处理触摸结束事件"""
        try:
            # 检查是否应该触发滑动切换图片或点击切换按钮
            if self.touch_start_pos and self.touch_current_pos:
                # 计算总滑动距离
                dx = self.touch_current_pos.x() - self.touch_start_pos.x()
                dy = self.touch_current_pos.y() - self.touch_start_pos.y()

                # 计算移动距离
                move_distance = abs(dx) + abs(dy)

                # 如果移动距离很小（小于15像素），认为是点击而非滑动
                if move_distance < 15:
                    # 检查点击位置是否在按钮容器内
                    click_pos = self.mapFromGlobal(event.touchPoints()[0].screenPos().toPoint()) if event.touchPoints() else self.touch_current_pos.toPoint()

                    # 判断点击是否在按钮容器内
                    is_on_button = False
                    if self.all_buttons_container.isVisible():
                        button_rect = self.all_buttons_container.geometry()
                        if button_rect.contains(click_pos):
                            is_on_button = True

                    # 只有点击在空白区域时才切换按钮显示/隐藏
                    if not is_on_button:
                        self.toggle_touch_buttons()
                # 判断是否为快速水平滑动（切换图片）
                # 条件：水平距离超过阈值，且主要是水平方向，且没有被标记为平移
                elif (abs(dx) > self.swipe_threshold and
                    abs(dx) > abs(dy) * 1.5 and
                    not self.is_touch_panning):

                    if dx > 0:
                        # 向右滑动，显示上一张
                        self.show_previous_image()
                    else:
                        # 向左滑动，显示下一张
                        self.show_next_image()

            # 重置所有触摸状态
            self.touch_start_pos = None
            self.touch_current_pos = None
            self.is_touch_swipe = False
            self.is_touch_panning = False

            # 延迟退出触摸模式，避免触发鼠标事件
            QTimer.singleShot(100, self.exit_touch_mode)

            event.accept()
            return True
        except Exception as e:
            print(f'触摸结束事件失败: {str(e)}')
        return False

    def exit_touch_mode(self):
        """退出触摸模式"""
        self.is_in_touch_mode = False

    def wheelEvent(self, event):
        try:
            if self.image:
                # 垂直滚动
                self.scroll_area.verticalScrollBar().setValue(
                    self.scroll_area.verticalScrollBar().value() - event.angleDelta().y()
                )
                event.accept()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标滚轮事件失败: {str(e)}')
            print(traceback.format_exc())

    def zoom_in(self):
        self.scale_image(1.1)

    def zoom_out(self):
        self.scale_image(0.9)

    def reset_zoom(self):
        try:
            if self.image:
                # 重置缩放因子
                self.scale_factor = 1.0
                
                # 将图片恢复到原始大小
                data = self.image.convert("RGBA").tobytes("raw", "RGBA")
                qim = QImage(data, self.image.width, self.image.height, QImage.Format_RGBA8888)
                self.pixmap = QPixmap.fromImage(qim)
                
                # 直接使用原始大小显示图片，不进行缩放
                self.image_label.setPixmap(self.pixmap)
                self.image_label.resize(self.pixmap.size())
                
                # 重置滚动条位置
                self.scroll_area.horizontalScrollBar().setValue(0)
                self.scroll_area.verticalScrollBar().setValue(0)
                
                # 显示提示信息
                QMessageBox.information(self, '提示', '图片已恢复原始大小')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'重置缩放失败: {str(e)}')
            print(traceback.format_exc())

    def scale_image(self, factor):
        try:
            if self.image:
                new_scale = self.scale_factor * factor
                
                # 确保缩放比例在允许范围内
                if self.min_scale <= new_scale <= self.max_scale:
                    self.scale_factor = new_scale
                    self.display_image()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'缩放图片失败: {str(e)}')
            print(traceback.format_exc())

    def load_image(self, file_path):
        try:
            self.image = Image.open(file_path)
            self.last_save_path = file_path
            self.current_image_path = file_path  # 设置当前图片路径
            self.add_to_history()
            self.display_image()
            self.showMaximized()

            # 更新图片列表
            self.update_image_list()

            # 更新窗口标题显示图片名称
            self.update_window_title()
        except Exception as e:
            QMessageBox.critical(self, '错误', f'打开图片失败: {str(e)}')
            print(traceback.format_exc())

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        # 不再自动重新定位按钮，保持用户设置的位置

if __name__ == '__main__':
    try:
        # Windows 任务栏图标设置 - 在创建 QApplication 之前设置
        try:
            import ctypes
            myappid = 'imageviewer.app.v1'  # 自定义应用ID
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except:
            pass  # 非 Windows 系统会失败，忽略即可

        app = QApplication(sys.argv)

        # 设置应用程序图标
        icon_path = resource_path('1024x1024.png')
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            app.setWindowIcon(app_icon)

        image_path = None
        if len(sys.argv) > 1:
            image_path = sys.argv[1]

        viewer = ImageViewer(image_path=image_path)
        if not image_path:
            viewer.showMaximized()

        sys.exit(app.exec_())
    except Exception as e:
        print(f"程序发生错误: {str(e)}")
        print(traceback.format_exc())
