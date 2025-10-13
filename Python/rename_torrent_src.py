import os
import sys
import bencodepy
import re
import logging
import traceback
from datetime import datetime

def setup_logging():
    """设置日志系统"""
    base_path = get_base_path()
    log_file = os.path.join(base_path, 'torrent_rename.log')
    
    # 创建日志格式
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return logging.getLogger(__name__)

def get_base_path():
    """获取基础路径，兼容直接运行和PyInstaller打包后运行"""
    if getattr(sys, 'frozen', False):
        # 打包后的exe文件所在目录
        return os.path.dirname(sys.executable)
    else:
        # 脚本文件所在目录
        return os.path.dirname(os.path.abspath(__file__))

def sanitize_filename(filename):
    """清理文件名中的非法字符"""
    invalid_chars = r'[<>:"/\\|?*]'
    return re.sub(invalid_chars, '_', filename)

def extract_file_names(torrent_path):
    """从torrent文件中提取文件名"""
    try:
        logger = logging.getLogger(__name__)
        logger.info(f"开始解析torrent文件: {torrent_path}")
        
        with open(torrent_path, 'rb') as f:
            torrent_data = bencodepy.decode(f.read())
            if b'info' in torrent_data and b'files' in torrent_data[b'info']:
                file_names = [file[b'path'][0].decode('utf-8', errors='ignore') for file in torrent_data[b'info'][b'files']]
                logger.info(f"从torrent文件中提取到 {len(file_names)} 个文件名")
                return file_names
            elif b'info' in torrent_data and b'name' in torrent_data[b'info']:
                file_name = torrent_data[b'info'][b'name'].decode('utf-8', errors='ignore')
                logger.info(f"从torrent文件中提取到单文件名: {file_name}")
                return [file_name]
            else:
                logger.warning(f"torrent文件结构异常，未找到有效文件名: {torrent_path}")
                return []
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"解析torrent文件失败 {torrent_path}: {str(e)}")
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        return []

def rename_torrent_file(torrent_path):
    """重命名单个torrent文件"""
    logger = logging.getLogger(__name__)
    
    try:
        file_names = extract_file_names(torrent_path)
        if not file_names:
            logger.warning(f"未找到有效文件名：{torrent_path}")
            return False
        
        base_name = sanitize_filename(file_names[0])
        if not base_name.strip():
            logger.warning(f"文件名为空：{torrent_path}")
            return False
            
        new_torrent_name = os.path.join(os.path.dirname(torrent_path), base_name + '.torrent')
        
        # 处理同名文件：如果已存在，添加数字后缀
        counter = 1
        temp_name = new_torrent_name
        while os.path.exists(temp_name):
            name, ext = os.path.splitext(new_torrent_name)
            temp_name = f"{name}_duplicate_{counter}{ext}"
            counter += 1
            if counter > 100:  # 防止无限循环
                logger.error(f"重命名失败：无法为 {torrent_path} 找到合适的文件名")
                return False
        
        try:
            os.rename(torrent_path, temp_name)
            logger.info(f"已将 {os.path.basename(torrent_path)} 重命名为 {os.path.basename(temp_name)}")
            return True
        except OSError as e:
            logger.error(f"重命名失败：{e}")
            return False
    except Exception as e:
        logger.error(f"处理文件时发生未知错误 {torrent_path}: {str(e)}")
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        return False

def process_directory(directory_path, include_subdirs=False):
    """处理目录中的所有torrent文件"""
    logger = logging.getLogger(__name__)
    error_count = 0
    processed_count = 0
    
    try:
        if not os.path.exists(directory_path):
            logger.error(f"目录不存在：{directory_path}")
            return 0, 1  # 处理0个文件，1个错误
        
        if not os.path.isdir(directory_path):
            logger.error(f"路径不是目录：{directory_path}")
            return 0, 1  # 处理0个文件，1个错误
        
        logger.info(f"开始处理目录：{directory_path}")
        
        if include_subdirs:
            # 递归处理所有子目录
            logger.info("递归处理所有子目录")
            for root, dirs, files in os.walk(directory_path):
                for file in files:
                    if file.lower().endswith('.torrent'):
                        torrent_path = os.path.join(root, file)
                        if rename_torrent_file(torrent_path):
                            processed_count += 1
                        else:
                            error_count += 1
        else:
            # 只处理当前目录
            logger.info("仅处理当前目录")
            for file in os.listdir(directory_path):
                if file.lower().endswith('.torrent'):
                    torrent_path = os.path.join(directory_path, file)
                    if rename_torrent_file(torrent_path):
                        processed_count += 1
                    else:
                        error_count += 1
        
        logger.info(f"目录处理完成：成功处理 {processed_count} 个文件，遇到 {error_count} 个错误")
        return processed_count, error_count
    except Exception as e:
        logger.error(f"处理目录时发生未知错误 {directory_path}: {str(e)}")
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        return processed_count, error_count + 1

def main():
    """主函数"""
    # 设置日志
    logger = setup_logging()
    
    base_path = get_base_path()
    default_dir = os.path.join(base_path, 'temp')
    
    logger.info("=" * 50)
    logger.info("Torrent文件重命名工具启动")
    logger.info(f"工作目录: {base_path}")
    logger.info(f"默认目录: {default_dir}")
    logger.info("=" * 50)
    
    print("=" * 50)
    print("Torrent文件重命名工具")
    print("=" * 50)
    
    total_processed = 0
    total_errors = 0
    
    try:
        # 获取待处理路径
        input_dir = input(f"请输入包含 .torrent 文件的目录路径（直接回车使用默认路径 {default_dir}）：").strip()
        
        if not input_dir:
            # 使用默认目录，递归处理所有子目录
            input_dir = default_dir
            include_subdirs = True
            print(f"使用默认目录：{input_dir}")
            print("默认目录将递归处理所有子目录")
            logger.info(f"用户选择默认目录: {input_dir}, 递归处理子目录: {include_subdirs}")
        else:
            # 用户输入了目录，询问是否处理子目录
            input_dir = os.path.abspath(input_dir)
            subdir_choice = input("是否处理目标目录下的所有子目录？(y/N，直接回车为否)：").strip().lower()
            include_subdirs = (subdir_choice == 'y' or subdir_choice == 'yes')
            logger.info(f"用户输入目录: {input_dir}, 递归处理子目录: {include_subdirs}")
        
        # 处理目录
        processed, errors = process_directory(input_dir, include_subdirs)
        total_processed += processed
        total_errors += errors
        
        logger.info(f"任务完成: 成功处理 {total_processed} 个文件，遇到 {total_errors} 个错误")
        
        # 显示处理结果
        print(f"\n处理完成！成功处理 {total_processed} 个文件")
        if total_errors > 0:
            print(f"遇到 {total_errors} 个错误，请查看日志文件了解详情")
            log_path = os.path.join(base_path, 'torrent_rename.log')
            print(f"日志文件位置: {log_path}")
        
    except Exception as e:
        logger.error(f"主程序发生未知错误: {str(e)}")
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        print(f"\n程序发生错误: {str(e)}")
        print("请查看日志文件了解详情")
    
    # 在打包成exe时暂停，直接运行时不暂停
    if getattr(sys, 'frozen', False):
        input("\n按回车键退出...")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger = logging.getLogger(__name__)
        logger.info("用户中断操作")
        print("\n\n用户中断操作")
    except Exception as e:
        # 如果在main函数之外发生错误，尝试记录日志
        try:
            logger = setup_logging()
            logger.error(f"程序启动失败: {str(e)}")
            logger.debug(f"详细错误信息: {traceback.format_exc()}")
        except:
            pass  # 如果日志系统也失败了，至少显示错误信息
        print(f"\n程序发生错误: {str(e)}")
        if getattr(sys, 'frozen', False):
            input("按回车键退出...")
