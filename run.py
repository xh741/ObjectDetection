# 数据需准备成以下格式(标准YOLO格式)
# ├── 数据集路径:data_path
#     └── image:存放所有图片
#     └── label:存放所有图片的标签，名称:图片名.txt，内容:(类别号 x_center y_center w h\n)相对图片的比例值
#     └── train.txt:训练图片的绝对路径(或相对data_path下路径)
#     └── val.txt:验证图片的绝对路径(或相对data_path下路径)
#     └── class.txt:所有的类别名称
# class.csv内容如下:
# 类别1
# 类别2
# ...
# -------------------------------------------------------------------------------------------------------------------- #
import os
import wandb
import torch
import argparse
from block.data_get import data_get
from block.model_get import model_get
from block.loss_get import loss_get
from block.train_get import train_get

# -------------------------------------------------------------------------------------------------------------------- #
# 分布式训练:
# python -m torch.distributed.launch --master_port 9999 --nproc_per_node n run.py --distributed True
# master_port为GPU之间的通讯端口，空闲的即可
# n为GPU数量
# -------------------------------------------------------------------------------------------------------------------- #
# 设置
parser = argparse.ArgumentParser(description='|目标检测|')
parser.add_argument('--data_path', default=r'D:\dataset\ObjectDetection\test', type=str, help='|数据根目录路径|')
parser.add_argument('--wandb', default=False, type=bool, help='|是否使用wandb可视化|')
parser.add_argument('--wandb_project', default='test', type=str, help='|wandb项目名称|')
parser.add_argument('--wandb_name', default='train', type=str, help='|wandb项目中的训练名称|')
parser.add_argument('--wandb_image_num', default=16, type=int, help='|wandb保存展示图片的数量|')
parser.add_argument('--weight', default='last.pt', type=str, help='|已有模型的位置，如果没找到模型才会创建剪枝模型/新模型|')
parser.add_argument('--save_path', default='best.pt', type=str, help='|最佳模型的保存位置，除此之外每轮结束都会保存last.pt|')
parser.add_argument('--prune', default=False, type=bool, help='|模型剪枝后再训练(部分模型有)，需要提供已经训练好的prune_weight|')
parser.add_argument('--prune_ratio', default=0.5, type=float, help='|模型剪枝时的保留比例|')
parser.add_argument('--prune_weight', default='best.pt', type=str, help='|模型剪枝时使用的模型，会创建剪枝模型和训练模型|')
parser.add_argument('--prune_save', default='prune_best.pt', type=str, help='|最佳模型的保存位置，除此之外每轮结束都会保存prune_last.pt|')
parser.add_argument('--model', default='yolov7', type=str, help='|模型选择|')
parser.add_argument('--model_type', default='n', type=str, help='|模型的型号参数，部分模型有|')
parser.add_argument('--input_size', default=640, type=int, help='|输入图片大小|')
parser.add_argument('--output_class', default=20, type=int, help='|输出的类别数|')
parser.add_argument('--loss_weight', default=((1 / 3, 0.3, 0.5, 0.2), (1 / 3, 0.4, 0.4, 0.2), (1 / 3, 0.5, 0.3, 0.2)),
                    type=tuple, help='|每个输出层(从大到小排序)的权重->[总权重、边框权重、置信度权重、分类权重]|')
parser.add_argument('--label_smooth', default=(0.01, 0.99), type=tuple, help='|标签平滑的值|')
parser.add_argument('--epoch', default=300, type=int, help='|训练轮数，继续训练时为增加的训练轮数|')
parser.add_argument('--batch', default=8, type=int, help='|训练批量大小|')
parser.add_argument('--lr_start', default=0.001, type=float, help='|初始学习率，训练中采用adam算法，前3轮有预热训练，基准为0.001|')
parser.add_argument('--lr_end', default=0.0001, type=float, help='|最终学习率，基准为0.0001|')
parser.add_argument('--lr_adjust_num', default=200, type=int, help='|从初始学习率到最终学习率经过的调整次数，余玄下降法|')
parser.add_argument('--lr_adjust_threshold', default=0.97, type=float, help='|本轮训练损失大于上一轮损失的比例时才调整，基准为0.97|')
parser.add_argument('--regularization', default='L2', type=str, help='|正则化，有L2、None|')
parser.add_argument('--r_value', default=0.0005, type=float, help='|正则化的权重系数|')
parser.add_argument('--device', default='cuda', type=str, help='|训练设备|')
parser.add_argument('--latch', default=True, type=bool, help='|模型和数据是否为锁存，True为锁存|')
parser.add_argument('--num_worker', default=0, type=int, help='|CPU在处理数据时使用的进程数，0表示只有一个主进程，一般为0、2、4、8|')
parser.add_argument('--ema', default=True, type=bool, help='|使用平均指数移动(EMA)调整参数|')
parser.add_argument('--amp', default=False, type=bool, help='|混合float16精度训练，windows上可能会出现nan，但linux正常|')
parser.add_argument('--mosaic', default=0.5, type=float, help='|使用mosaic增强的概率|')
parser.add_argument('--mosaic_hsv', default=0.5, type=float, help='|mosaic增强时的hsv通道随机变换概率|')
parser.add_argument('--mosaic_flip', default=0.5, type=float, help='|mosaic增强时的垂直翻转概率|')
parser.add_argument('--mosaic_screen', default=10, type=int, help='|mosaic增强后留下的框w,h不能小于mosaic_screen，根据物体大小调整|')
parser.add_argument('--confidence_threshold', default=0.35, type=float, help='|指标计算置信度阈值|')
parser.add_argument('--iou_threshold', default=0.5, type=float, help='|指标计算iou阈值|')
parser.add_argument('--distributed', default=False, type=bool, help='|单机多卡分布式训练，分布式训练时batch为总batch|')
parser.add_argument('--local_rank', default=0, type=int, help='|分布式训练使用命令后会自动传入的参数|')
args = parser.parse_args()
args.gpu_number = torch.cuda.device_count()  # 使用的GPU数
# 为CPU设置随机种子
torch.manual_seed(999)
# 为所有GPU设置随机种子
torch.cuda.manual_seed_all(999)
# 固定每次返回的卷积算法
torch.backends.cudnn.deterministic = True
# cuDNN使用非确定性算法
torch.backends.cudnn.enabled = True
# 训练前cuDNN会先搜寻每个卷积层最适合实现它的卷积算法，加速运行；但对于复杂变化的输入数据，可能会有过长的搜寻时间，对于训练比较快的网络建议设为False
torch.backends.cudnn.benchmark = False
# wandb可视化:https://wandb.ai
if args.wandb and args.local_rank == 0:  # 分布式时只记录一次wandb
    args.wandb_run = wandb.init(project=args.wandb_project, name=args.wandb_name, config=args)
# 混合float16精度训练
if args.amp:
    args.amp = torch.cuda.amp.GradScaler()
# 分布式训练
if args.distributed:
    torch.distributed.init_process_group(backend="nccl")  # 分布式训练初始化
    args.device = torch.device("cuda", args.local_rank)
# -------------------------------------------------------------------------------------------------------------------- #
# 初步检查
if args.local_rank == 0:
    print(f'| args:{args} |')
    assert os.path.exists(f'{args.data_path}/image'), 'data_path中缺少:image'
    assert os.path.exists(f'{args.data_path}/label'), 'data_path中缺少:label'
    assert os.path.exists(f'{args.data_path}/train.txt'), 'data_path中缺少:train.txt'
    assert os.path.exists(f'{args.data_path}/val.txt'), 'data_path中缺少:val.txt'
    assert os.path.exists(f'{args.data_path}/class.txt'), 'data_path中缺少:class.txt'
    if os.path.exists(args.weight):  # 优先加载已有模型args.weight继续训练
        print(f'| 加载已有模型:{args.weight} |')
    elif args.prune:
        print(f'| 加载模型+剪枝训练:{args.prune_weight} |')
    else:  # 创建自定义模型args.model
        assert os.path.exists(f'model/{args.model}.py'), f'没有此自定义模型:{args.model}'
        print(f'| 创建自定义模型:{args.model} | 型号:{args.model_type} |')
# -------------------------------------------------------------------------------------------------------------------- #
# 程序
if __name__ == '__main__':
    # 数据(只是图片路径和原始txt中标签，读取和预处理在训练/验证中完成)
    data_dict = data_get(args)
    # 模型
    model_dict = model_get(args)
    # 损失
    loss = loss_get(args)
    # 摘要
    print('| 训练集:{} | 验证集:{} | 批量{} | 模型:{} | 输入尺寸:{} | 初始学习率:{} |'
          .format(len(data_dict['train']), len(data_dict['val']), args.batch, args.model, args.input_size,
                  args.lr_start)) if args.local_rank == 0 else None
    # 训练
    train_get(args, data_dict, model_dict, loss)
