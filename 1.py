import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QAction, QFileDialog, 
                            QLabel, QInputDialog, QMessageBox, QColorDialog, QScrollArea)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QImage, QPen, QCursor, QIcon
from PyQt5.QtCore import Qt, QPoint, QTemporaryFile, QEvent
from PIL import Image, ImageDraw
import numpy as np
import traceback

VERSION = "2025/7/23-01"

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
        undo_action.triggered.connect(self.undo)
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            self.paste_image()
        elif event.key() == Qt.Key_Z and event.modifiers() == Qt.ControlModifier:
            self.undo()
        elif event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self.copy_image()
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
                self.add_to_history()
                self.display_image()
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
            self.add_to_history()
            self.display_image()
            self.showMaximized()
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
