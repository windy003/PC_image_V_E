import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QAction, QFileDialog, 
                            QLabel, QInputDialog, QMessageBox, QColorDialog, QScrollArea, QLineEdit)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QImage, QPen, QCursor, QIcon
from PyQt5.QtCore import Qt, QPoint
from PIL import Image, ImageDraw
import numpy as np
import traceback
import logging
import datetime

VERSION = "2025/2/22-01"

def setup_logging():
    try:
        # 创建日志文件名，包含时间戳
        log_filename = f'image_viewer_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        
        # 配置日志
        logging.basicConfig(
            filename=log_filename,
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # 同时将日志输出到控制台
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        logging.getLogger('').addHandler(console_handler)
        
        logging.info("日志系统初始化成功")
    except Exception as e:
        print(f"设置日志系统失败: {str(e)}")

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
    def __init__(self):
        super().__init__()
        self.last_save_path = ''  # 添加变量记录上次保存路径
        self.color_label = None  # 添加颜色标签
        self.initUI()

    def initUI(self):
        try:
            # 设置带版本号的窗口标题
            self.setWindowTitle(f'图片查看和编辑工具 v{VERSION}')
            self.setGeometry(100, 100, 800, 600)

            # 设置应用图标
            icon_path = resource_path('icon.ico')
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

            # 创建一个可选择的颜色标签
            self.statusBar()
            self.color_label = QLineEdit()
            self.color_label.setReadOnly(True)  # 设置为只读
            self.color_label.setStyleSheet("""
                QLineEdit {
                    border: none;
                    background: transparent;
                    color: black;
                    font-size: 12pt;  /* 字体大小 */
                    font-family: Arial;  /* 设置字体 */
                    padding: 2px 10px;  /* 左右内边距 */
                    min-width: 300px;  /* 最小宽度 */
                }
            """)
            self.color_label.setText('颜色: #------ 坐标: (-,-)')
            self.statusBar().addPermanentWidget(self.color_label, 1)  # 添加拉伸因子1
            
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

            # 创建菜单栏
            self.create_menus()
            
            # 初始化历史记录
            self.history = []
            self.current_step = -1

            self.showMaximized()
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
                logging.debug("无效的图像或pixmap")
                return None, None

            # 获取图像标签的几何信息
            label_rect = self.image_label.geometry()
            pixmap = self.image_label.pixmap()
            
            if not pixmap:
                logging.debug("无效的pixmap")
                return None, None

            # 获取图像标签的实际显示区域
            label_width = label_rect.width()
            label_height = label_rect.height()
            
            # 获取图像的原始尺寸
            image_width = self.image.width
            image_height = self.image.height
            
            # 计算缩放后的图像尺寸
            scaled_width = int(image_width * self.scale_factor)
            scaled_height = int(image_height * self.scale_factor)
            
            # 计算图像在标签中的居中偏移
            x_offset = (label_width - scaled_width) // 2
            y_offset = (label_height - scaled_height) // 2
            
            # 计算相对于图像的点击位置
            image_x = (pos.x() - x_offset) / self.scale_factor
            image_y = (pos.y() - y_offset) / self.scale_factor
            
            # 确保坐标在图像范围内
            image_x = max(0, min(int(image_x), image_width - 1))
            image_y = max(0, min(int(image_y), image_height - 1))
            
            logging.debug(f"坐标转换: 标签大小={label_width}x{label_height}, "
                         f"图像大小={image_width}x{image_height}, "
                         f"缩放比例={self.scale_factor}, "
                         f"偏移量=({x_offset}, {y_offset}), "
                         f"最终坐标=({image_x}, {image_y})")
            
            return image_x, image_y

        except Exception as e:
            logging.error(f"坐标转换错误: {str(e)}", exc_info=True)
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
            if event.button() == Qt.LeftButton:
                pos = self.image_label.mapFrom(self, event.pos())
                logging.debug(f"鼠标点击位置: {event.pos()}, 映射到标签位置: {pos}")
                
                # 只获取颜色信息
                try:
                    self.try_get_color(pos)
                except Exception as e:
                    logging.error(f"获取颜色失败: {str(e)}")
                    self.color_label.setText('颜色: #------ 坐标: (-,-)')

        except Exception as e:
            logging.error(f"鼠标按下事件错误: {str(e)}", exc_info=True)

    def try_get_color(self, pos):
        """安全地尝试获取颜色信息"""
        try:
            if not self.image:
                logging.debug("没有加载图像")
                return
            if not pos:
                logging.debug("无效的位置信息")
                return

            logging.debug(f"开始获取颜色，位置: {pos.x()}, {pos.y()}")
            
            x, y = self.get_image_coordinates(pos)
            if x is None or y is None:
                logging.debug("无法获取有效的图像坐标")
                return

            # 确保坐标在图像范围内
            if not (0 <= x < self.image.width and 0 <= y < self.image.height):
                logging.debug(f"坐标超出范围: ({x}, {y}), 图像大小: {self.image.width}x{self.image.height}")
                return

            try:
                # 从QPixmap获取颜色
                if self.image_label.pixmap():
                    qimage = self.image_label.pixmap().toImage()
                    if qimage.valid(int(x), int(y)):
                        color = QColor(qimage.pixel(int(x), int(y)))
                        color_hex = '#{:02X}{:02X}{:02X}'.format(
                            color.red(), color.green(), color.blue())
                        self.color_label.setText(f'颜色: {color_hex} 坐标: ({x}, {y})')
                        logging.debug(f"设置颜色标签: {color_hex}")
                    else:
                        logging.debug("无效的图像坐标")
                        self.color_label.setText('颜色: #------ 坐标: (-,-)')
                else:
                    logging.debug("无效的pixmap")
                    self.color_label.setText('颜色: #------ 坐标: (-,-)')

            except Exception as e:
                logging.error(f"获取或处理像素值失败: {str(e)}", exc_info=True)
                self.color_label.setText('颜色: #------ 坐标: (-,-)')

        except Exception as e:
            logging.error(f"获取颜色过程出错: {str(e)}", exc_info=True)
            self.color_label.setText('颜色: #------ 坐标: (-,-)')

    def mouseMoveEvent(self, event):
        try:
            if not self.image:  # 首先检查是否有图像
                return

            if self.panning and self.last_pan_pos:
                try:
                    # 计算移动距离
                    delta = event.pos() - self.last_pan_pos
                    # 更新滚动条位置
                    hbar = self.scroll_area.horizontalScrollBar()
                    vbar = self.scroll_area.verticalScrollBar()
                    
                    if hbar and vbar:  # 确保滚动条存在
                        hbar.setValue(hbar.value() - delta.x())
                        vbar.setValue(vbar.value() - delta.y())
                    self.last_pan_pos = event.pos()
                except Exception as e:
                    print(f"平移操作错误: {str(e)}")
                    self.panning = False
                    self.last_pan_pos = None
                
            elif self.drawing:
                try:
                    pos = self.image_label.mapFrom(self, event.pos())
                    if pos:  # 确保pos有效
                        self.apply_effect(pos)
                        self.last_point = pos
                except Exception as e:
                    print(f"绘画操作错误: {str(e)}")
                    self.drawing = False
                    self.last_point = None

        except Exception as e:
            print(f"鼠标移动事件错误: {str(e)}")
            # 重置所有状态
            self.drawing = False
            self.panning = False
            self.last_pan_pos = None
            self.last_point = None
            self.color_label.setText('颜色: #------ 坐标: (-,-)')

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                if self.panning:
                    self.panning = False
                    self.last_pan_pos = None
                    self.setCursor(Qt.ArrowCursor)
                elif self.drawing:
                    self.drawing = False
                    self.last_point = None
                    self.display_image()
        except Exception as e:
            print(f"鼠标释放事件错误: {str(e)}")
            # 重置所有状态
            self.drawing = False
            self.panning = False
            self.last_pan_pos = None
            self.last_point = None

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
                # 使用上次的保存路径作为打开对话框的默认路径
                initial_path = self.last_save_path if self.last_save_path else ''
                file_path, _ = QFileDialog.getSaveFileName(
                    self, 
                    '保存图片', 
                    initial_path,  # 使用记住的路径
                    'Images (*.png *.jpg *.jpeg *.bmp)'
                )
                
                if file_path:
                    self.image.save(file_path)
                    self.last_save_path = file_path  # 记住这次的保存路径
                    QMessageBox.information(self, '提示', '图片保存成功')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存图片失败: {str(e)}')
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
            if clipboard.mimeData().hasImage():
                qimage = clipboard.image()
                if not qimage.isNull():
                    # 将QImage转换为正确的格式
                    if qimage.format() != QImage.Format_RGBA8888:
                        qimage = qimage.convertToFormat(QImage.Format_RGBA8888)
                    
                    # 获取图像数据
                    width = qimage.width()
                    height = qimage.height()
                    ptr = qimage.constBits()
                    ptr.setsize(height * width * 4)
                    arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 4))
                    
                    # 使用正确的颜色通道顺序创建PIL图像
                    self.image = Image.fromarray(arr, 'RGBA')
                    self.add_to_history()
                    self.display_image()
                    QMessageBox.information(self, '提示', '图片已从剪贴板粘贴')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'粘贴图片失败: {str(e)}')
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

    def wheelEvent(self, event):
        try:
            if self.image:
                # 获取鼠标滚轮的delta值
                delta = event.angleDelta().y()
                
                # 根据滚轮方向调整缩放
                if delta > 0:
                    self.zoom_in()
                else:
                    self.zoom_out()
                
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

if __name__ == '__main__':
    try:
        setup_logging()
        logging.info("程序启动")
        
        app = QApplication(sys.argv)
        
        # 设置应用程序图标
        icon_path = resource_path('icon.ico')
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            app.setWindowIcon(app_icon)
        
        viewer = ImageViewer()
        viewer.show()
        
        exit_code = app.exec_()
        logging.info(f"程序正常退出，退出码: {exit_code}")
        sys.exit(exit_code)
    except Exception as e:
        logging.critical(f"程序发生致命错误: {str(e)}", exc_info=True)
        print(f"程序发生错误: {str(e)}")
        print(traceback.format_exc())
