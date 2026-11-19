import os
import matplotlib.pyplot as plt


# trainer.tloss: tensor (ultralytics <= 8.3) or {'box': .., 'cls': .., 'dfl': ..} (8.4~)
def total_loss(tloss):
    if tloss is None:
        return 0.0
    if isinstance(tloss, dict):
        return float(sum(float(v) for v in tloss.values()))
    return float(tloss.sum())


# live training curve, updated every epoch
# usage: model.add_callback('on_fit_epoch_end', LivePlot(show=True))
# - always saved to {run dir}/live.png (open it in VS Code -> refreshes when the file changes)
# - show=True -> also opens a window (needs a display, not on Colab / remote server)
class LivePlot:
    def __init__(self, show=False):
        self.show = show
        if show:
            plt.ion()
        else:
            plt.switch_backend('Agg')   # no window
        self.fig, (self.ax_loss, self.ax_map) = plt.subplots(1, 2, figsize=(11, 4))
        self.epochs, self.loss, self.map50, self.map50_95 = [], [], [], []

    # called by ultralytics after each epoch (train + val)
    def __call__(self, trainer):
        epoch = trainer.epoch + 1
        if self.epochs and self.epochs[-1] == epoch:   # final re-validation of best.pt -> skip
            return

        self.epochs.append(epoch)
        self.loss.append(total_loss(trainer.tloss))
        self.map50.append(trainer.metrics.get('metrics/mAP50(B)', 0))
        self.map50_95.append(trainer.metrics.get('metrics/mAP50-95(B)', 0))

        self.ax_loss.clear()
        self.ax_loss.plot(self.epochs, self.loss, 'o-')
        self.ax_loss.set_title('train loss')
        self.ax_loss.set_xlabel('epoch')

        self.ax_map.clear()
        self.ax_map.plot(self.epochs, self.map50, 'o-', label='mAP@50')
        self.ax_map.plot(self.epochs, self.map50_95, 'o-', label='mAP@50:95')
        self.ax_map.set_title(f'val mAP (best @50: {max(self.map50):.3f})')
        self.ax_map.set_xlabel('epoch')
        self.ax_map.set_ylim(0, 1)
        self.ax_map.legend()

        self.fig.suptitle(os.path.basename(trainer.save_dir))
        self.fig.tight_layout()
        self.fig.savefig(os.path.join(trainer.save_dir, 'live.png'))
        if self.show:
            plt.pause(0.01)   # redraw window
