import matplotlib
# matplotlib.use('Agg')

from baselines.template.util import store_args, logger
from baselines.template.rollout import Rollout
import time
from tqdm import tqdm
import os
import matplotlib.pyplot as plt
from mujoco_py import MujocoException
import numpy as np
from matplotlib import pyplot as plt
from matplotlib import animation
from collections import deque
from baselines.template.util import convert_episode_to_batch_major, store_args
import matplotlib.pyplot as plt
from mujoco_py.generated import const as mj_const
from plot.plot_model_train import plot_model_train
from multiprocessing import Process, Queue
import imageio


class RolloutWorker(Rollout):
    @store_args
    def __init__(self, make_env, policy, dims, logger, T, **kwargs):
        """Rollout worker generates experience by interacting with one or many environments.
        """
        Rollout.__init__(self, make_env, policy, dims, logger, T, **kwargs)

        self.err = None
        self.err_std = None
        self.pred = None
        self.loss_histories = []
        self.surprise_fig = None
        self.replayed_episodes = []
        self.top_exp_replay_values = deque(maxlen=10)
        self.episodes_per_epoch = None
        self.visualize_replay = False
        self.record_replay = True
        self.do_plot = False

    def logs(self, prefix='worker'):
        """Generates a dictionary that contains all collected statistics.
        """
        logs = []
        logs += [('success_rate', np.mean(self.success_history))]
        for i,l in enumerate(self.loss_histories):
            loss_key = 'loss-{}'.format(i)
            loss_key_map = ['total loss', 'observation loss', 'loss prediction loss']
            if i < len(loss_key_map):
                loss_key = loss_key_map[i]
            if len(l) > 0:
                logs += [(loss_key, l[-1])]
            if len(l) > 1:
                loss_grad = l[-1] / l[-2]
            else:
                loss_grad = np.nan
            logs += [('{} grad'.format(loss_key), loss_grad)]

        if self.err is not None :
            logs += [('pred_err', self.err)]
        if self.pred is not None:
            logs += [('pred_steps', self.pred)]
        logs += [('episode', self.n_episodes)]

        return logger(logs, prefix)

    def generate_rollouts_update(self, n_cycles, n_batches):
        dur_ro = 0
        dur_train = 0
        dur_start = time.time()
        avg_epoch_losses = []
        last_episode_batch = None
        self.episodes_per_epoch = n_cycles
        for cyc in range(n_cycles):
            print("episode {} / {}".format(cyc, n_cycles))
            ro_start = time.time()
            episode, mj_states = self.generate_rollouts(return_states=True)
            last_episode_batch = episode
            stored_idxs = self.policy.store_episode(episode, mj_states=mj_states)
            for i in stored_idxs:
                if i in self.replayed_episodes:
                    self.replayed_episodes.remove(i)
            dur_ro += time.time() - ro_start
            train_start = time.time()
            for _ in range(n_batches):
                losses = self.policy.train()
                if not isinstance(losses, tuple):
                    losses = [losses]

                if self.loss_histories == []:
                    self.loss_histories = [deque(maxlen=2) for _ in losses]
                if avg_epoch_losses == []:
                    avg_epoch_losses = [0 for _ in losses]
                for idx, loss in enumerate(losses):
                    avg_epoch_losses[idx] += loss
            dur_train += time.time() - train_start
        for idx,loss in enumerate(avg_epoch_losses):
            avg_loss = loss / n_cycles
            self.loss_histories[idx].append(avg_loss)
        self.test_prediction_error(last_episode_batch)
        dur_total = time.time() - dur_start
        updated_policy = self.policy
        time_durations = (dur_total, dur_ro, dur_train)
        self.replay_experience()
        if self.do_plot:
            plot_model_train("/".join(self.logger.get_dir().split("/")[:-1]))
        return updated_policy, time_durations


    def test_prediction_error(self, episode):
        this_err_hist = []
        this_std_hist = []
        this_pred_hist = []
        for i1, eps_o in enumerate(episode['o']):
            transitions = []
            for i2,ep_o in enumerate(eps_o[:-1]):
                o = eps_o[i2]
                o2 = eps_o[i2 + 1]
                u = episode['u'][i1][i2]
                transitions.append({"o": o, "o2": o2, "u": u})

            # Test error for each individual transition
            ep_err_hist = []
            s = None
            for t in transitions:
                o = t['o']
                u = t['u']
                o2 = t['o2']
                o2_pred, l, s = self.policy.forward_step_single(u,o,s)
                err = np.mean(abs(o2 - o2_pred))
                ep_err_hist.append(err)
            ep_err_mean = np.mean(ep_err_hist)
            ep_err_std = np.std(ep_err_hist)
            this_err_hist.append(ep_err_mean)
            this_std_hist.append(ep_err_std)

            # Test how many steps can be predicted without the error exceeding the goal achievement threshold.
            o = transitions[0]['o']
            step = 0
            s = None
            for t in transitions[:-1]:
                step += 1
                u = t['u']
                o2 = t['o2']
                o, l, s = self.policy.forward_step_single(u, o, s)
                o_pred_g = self.policy.env._obs2goal(o)
                o_g = self.policy.env._obs2goal(o2)
                pred_success = self.policy.env._is_success(o_pred_g, o_g)
                if not pred_success:
                    break
            this_pred_hist.append(step-1)

        err_mean = np.mean(this_err_hist)
        err_std_mean = np.mean(this_std_hist)
        pred_mean = np.mean(this_pred_hist)
        self.err = err_mean
        self.err_std = err_std_mean
        self.pred = pred_mean

    def init_surprise_plot(self):
        self.surprise_fig = plt.figure(figsize=(10, 4), dpi=70)
        self.surprise_fig_ax = self.surprise_fig.add_subplot(111)
        self.surprise_fig_ax.set_facecolor('white')
        self.surprise_fig_ax.grid(color='gray', linestyle='-', linewidth=1)
        self.surprise_fig_ax.spines['left'].set_color('black')
        self.surprise_fig_ax.spines['bottom'].set_color('black')
        self.surprise_fig_ax.set_xlim([0, self.T])

        # init with some X and Y data
        x = [0]
        y = [0]
        self.surprise_fig_li_loss, = self.surprise_fig_ax.plot(x, y,color=(0,0,0), label='surprise')
        self.surprise_fig_li_pred_loss, = self.surprise_fig_ax.plot(x, y, color=(1, 0, 0), label='predicted surprise')
        self.surprise_fig_ax.relim()
        self.surprise_fig_ax.autoscale_view(True, True, True)
        # plt.ylabel('surprise')
        plt.pause(0.01)
        self.surprise_fig.canvas.draw()
        plt.pause(0.01)
        # if self.visualize_replay:
        plt.show(block=False)
        legend = plt.legend(frameon=0, loc='upper left')
        frame = legend.get_frame()
        frame.set_color('white')
        frame.set_linewidth(0)
        plt.pause(0.01)

    def replay_experience(self):
        if self.visualize_replay is False:
            plt.switch_backend('agg')
        if self.surprise_fig is None:
            self.init_surprise_plot()

        current_epoch = int(np.round(self.policy.model_replay_buffer.ep_no / self.episodes_per_epoch)) - 1

        last_added_idxs = np.argwhere(self.policy.model_replay_buffer.ep_added > (self.policy.model_replay_buffer.ep_no - self.episodes_per_epoch))
        last_added_idxs = last_added_idxs.flatten()
        last_added_values = np.take(self.policy.model_replay_buffer.memory_value, last_added_idxs)
        replay_idx = last_added_idxs[np.argmax(last_added_values)]

        highest_mem_val = self.policy.model_replay_buffer.memory_value[replay_idx]
        if len(self.top_exp_replay_values) == self.top_exp_replay_values.maxlen:
            mem_val_required = min(self.top_exp_replay_values)
        else:
            mem_val_required = 0

        if highest_mem_val < mem_val_required:
            print("highes mem_val is {}, but {} required to be interesting enough for replay".format(highest_mem_val, mem_val_required))
            self.top_exp_replay_values = deque(np.array(self.top_exp_replay_values) * 0.95, maxlen=self.top_exp_replay_values.maxlen)
            return

        if replay_idx in self.replayed_episodes:
            return

        self.replayed_episodes.append(replay_idx)
        self.top_exp_replay_values.append(highest_mem_val)

        ep_added = self.policy.model_replay_buffer.ep_added[replay_idx]
        mem_val = self.policy.model_replay_buffer.memory_value[replay_idx]

        print("Replaying experience {} with highest memory value {}, added in episode {}.".format(replay_idx, mem_val, ep_added))

        replay_video_fpath = os.path.join(self.logger.get_dir(), "v_{:.2f}_ep_{}_.mp4".format(highest_mem_val, current_epoch))

        buff_idxs = [replay_idx]
        env = self.envs[0].env

        for buff_idx in buff_idxs:
            if current_epoch > 0:
                print("ep {}".format(current_epoch))

            if self.record_replay:
                frames = []
                plots = []

            for step_no, mj_state in enumerate(self.policy.model_replay_buffer.mj_states[buff_idx]):
                u = self.policy.model_replay_buffer.buffers['u'][buff_idx][step_no]
                next_o, _, _, _ = env.step(u)
                # env.sim.set_state(self.policy.model_replay_buffer.mj_states[buff_idx][step_no])
                env.sim.set_state(mj_state)

                surprise_hist = self.policy.model_replay_buffer.buffers['loss'][buff_idx][:step_no+1]
                pred_surprise_hist = self.policy.model_replay_buffer.buffers['loss_pred'][buff_idx][:step_no+1]
                # surprise = surprise_hist[-1]
                steps = list(range(step_no+1))

                plt.pause(0.0001)
                try:
                    self.surprise_fig_li_loss.set_xdata(steps)
                    self.surprise_fig_li_pred_loss.set_xdata(steps)
                except Exception as e:
                    print("Something went wrong: {}".format(e))
                self.surprise_fig_li_loss.set_ydata(surprise_hist)
                self.surprise_fig_li_pred_loss.set_ydata(pred_surprise_hist)
                self.surprise_fig_ax.relim()
                self.surprise_fig_ax.autoscale_view(True, True, True)

                step_no += 1
                self.surprise_fig.canvas.draw()

                if self.visualize_replay:
                    env.render()

                if self.record_replay:
                    record_viewer = env._get_viewer('rgb_array')
                    frame = record_viewer._read_pixels_as_in_window()
                    fig = self.surprise_fig
                    frame_plot = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep='')
                    frame_plot = frame_plot.reshape(fig.canvas.get_width_height()[::-1] + (3,))
                    plots.append(frame_plot)
                    frames.append(frame)
            if self.record_replay and len(frames) > 0:
                video_writer = imageio.get_writer(replay_video_fpath, fps=10)
                # frame_writer = imageio.get_writer(replay_video_fpath+"_frame.mp4", fps=10)
                # mask_writer = imageio.get_writer(replay_video_fpath + "_mask.mp4", fps=10)
                # plot_writer = imageio.get_writer(replay_video_fpath + "_plot.mp4", fps=10)
                for frame, plot in zip(frames, plots):
                    # frame_writer.append_data(frame)
                    f_width = frame.shape[1]
                    f_height = frame.shape[0]
                    p_width = plot.shape[1]
                    p_height = plot.shape[0]
                    pad_values = ((0, f_height-p_height),(f_width-p_width,0), (0,0))
                    plot_mask = np.where(plot == 255, np.uint8(1), np.uint8(0))
                    padded_plot_mask = np.pad(plot_mask, pad_values, 'constant', constant_values=1)
                    # mask_writer.append_data(padded_plot_mask * 100)
                    large_plot = np.pad(plot, pad_values, 'constant', constant_values=0)
                    masked_large_plot = large_plot * padded_plot_mask
                    # plot_writer.append_data(masked_large_plot * 100)
                    frame_with_plot = (frame * padded_plot_mask) + masked_large_plot
                    video_writer.append_data(frame_with_plot)
                video_writer.close()
                # frame_writer.close()
                # mask_writer.close()
                # plot_writer.close()







