import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QAction, QFileDialog,
                            QLabel, QInputDialog, QMessageBox, QColorDialog, QScrollArea)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QImage, QPen, QCursor, QIcon, QFont
from PyQt5.QtCore import Qt, QPoint, QTemporaryFile, QEvent, QTimer
from PIL import Image, ImageDraw
import numpy as np
import traceback

VERSION = "2025/11/9-01"

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
            self.current_tool = 'draw'  # 'draw' 或 'blur'
            self.brush_color = QColor(255, 0, 0)  # 默认红色 (RGB: 255, 0, 0)
            self.pixmap = None
            self.scale_factor = 1.0  # 添加缩放因子
            self.min_scale = 0.1  # 最小缩放比例
            self.max_scale = 5.0  # 最大缩放比例
            self.panning = False  # 添加平移状态标志
            self.last_pan_pos = None  # 添加上一次平移位置
            self.grabGesture(Qt.PinchGesture)
            self._pinch_start_scale_factor = 1.0

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

        except Exception as e:
            QMessageBox.critical(self, '错误', f'初始化失败: {str(e)}')
            print(traceback.format_exc())

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

            if not os.path.exists(self.current_image_path):
                self.show_notification("图片文件不存在")
                return

            # 记录删除的文件路径
            deleted_path = self.current_image_path
            filename = os.path.basename(deleted_path)

            # 创建临时备份文件（用于撤销）
            import tempfile
            import shutil
            temp_dir = tempfile.gettempdir()
            backup_path = os.path.join(temp_dir, f"image_backup_{filename}")

            # 备份文件
            shutil.copy2(deleted_path, backup_path)

            # 使用 Windows Shell API 移动到回收站
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            namespace = shell.NameSpace(0)

            # 规范化路径（解决 OneDrive 路径问题）
            normalized_path = os.path.normpath(os.path.abspath(deleted_path))

            # 使用 Shell 命令移动到回收站
            item = namespace.ParseName(normalized_path)
            if item:
                item.InvokeVerb("delete")  # 移动到回收站

                # 记录最后删除的文件，用于撤销
                self.last_deleted_file = {
                    'path': deleted_path,
                    'filename': filename,
                    'directory': os.path.dirname(deleted_path),
                    'backup_path': backup_path
                }

                print(f"Debug: File deleted, last_deleted_file set to: {self.last_deleted_file}")
                self.show_notification(f"已删除: {filename} (Ctrl+Z 可撤销)")

                # 清空当前图片
                self.image = None
                self.current_image_path = None
                self.image_label.clear()

                # 从列表中移除已删除的图片
                if deleted_path in self.image_list:
                    self.image_list.remove(deleted_path)
                    # 索引不需要更新，因为图片已清空
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

            # 支持的图片格式
            image_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')

            # 获取目录中所有图片文件
            all_files = []
            for file in os.listdir(directory):
                if file.lower().endswith(image_extensions):
                    full_path = os.path.normpath(os.path.join(directory, file))
                    all_files.append(full_path)

            # 按文件名排序
            all_files.sort()

            self.image_list = all_files

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

            print(f"Debug: Found {len(self.image_list)} images, current index: {self.current_image_index}")
            print(f"Debug: Current path: {current_normalized}")
            if self.image_list:
                print(f"Debug: First image in list: {self.image_list[0]}")

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
        """将当前图片复制到上层目录"""
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
            self.show_notification(f"已复制到: {os.path.basename(destination)}")

        except Exception as e:
            self.show_notification(f"复制失败: {str(e)}")
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
                if event.modifiers() == Qt.AltModifier:  # 按住Alt键进行平移
                    self.panning = True
                    self.last_pan_pos = event.pos()
                    self.setCursor(Qt.ClosedHandCursor)
                else:  # 正常的绘画操作
                    self.drawing = True
                    self.add_to_history()
                    pos = self.image_label.mapFrom(self, event.pos())
                    self.last_point = pos
                    self.apply_effect(pos)
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标按下事件失败: {str(e)}')
            print(traceback.format_exc())

    def mouseMoveEvent(self, event):
        try:
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
                self.apply_effect(pos)
                self.last_point = pos
        except Exception as e:
            QMessageBox.critical(self, '错误', f'鼠标移动事件失败: {str(e)}')
            print(traceback.format_exc())

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                if self.panning:
                    self.panning = False
                    self.last_pan_pos = None
                    self.setCursor(Qt.ArrowCursor)
                else:
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

    def set_tool(self, tool):
        self.current_tool = tool
        if tool == 'draw':
            QMessageBox.information(self, '工具切换', '已切换到涂鸦工具')
        else:
            QMessageBox.information(self, '工具切换', '已切换到模糊工具')

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
                image_name = os.path.basename(file_path)
                self.setWindowTitle(f'图片查看和编辑工具 v{VERSION} ----------------- {image_name}')
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
        return super(ImageViewer, self).event(event)

    def gestureEvent(self, event):
        pinch = event.gesture(Qt.PinchGesture)
        if pinch:
            if pinch.state() == Qt.GestureStarted:
                self._pinch_start_scale_factor = self.scale_factor
            elif pinch.state() == Qt.GestureUpdated:
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

            return True
        return False

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
            image_name = os.path.basename(file_path)
            self.setWindowTitle(f'图片查看和编辑工具 v{VERSION} ----------------- {image_name}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'打开图片失败: {str(e)}')
            print(traceback.format_exc())

if __name__ == '__main__':
    try:
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
