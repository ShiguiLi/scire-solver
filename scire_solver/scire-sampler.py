import torch
import torch.nn.functional as F
import math
import numpy as np


class NoiseScheduleVP:
    def __init__(
            self,
            schedule='linear',
            betas=None,
            alphas_cumprod=None,
            continuous_beta_0=0.1,
            continuous_beta_1=20.,
            # dtype=torch.float32,
            dtype=None,
    ):

        if schedule not in ['discrete', 'linear']:
            raise ValueError(
                "Unsupported noise schedule {}. The schedule needs to be 'discrete' or 'linear'".format(schedule))

        self.schedule = schedule
        if schedule == 'discrete':
            if betas is not None:
                log_alphas = 0.5 * torch.log(1 - betas).cumsum(dim=0)
                # alphas = torch.sqrt(1 - betas).cumsum(dim=0)
            else:
                assert alphas_cumprod is not None
                log_alphas = 0.5 * torch.log(alphas_cumprod)
            self.T = 1.
            self.log_alpha_array = self.numerical_clip_alpha(log_alphas).reshape((1, -1,)).to(dtype=dtype)
            self.total_N = self.log_alpha_array.shape[1]
            self.t_array = torch.linspace(0., 1., self.total_N + 1)[1:].reshape((1, -1)).to(dtype=dtype)
        else:
            self.T = 1.
            self.total_N = 1000
            self.beta_0 = continuous_beta_0
            self.beta_1 = continuous_beta_1

    def numerical_clip_alpha(self, log_alphas, clipped_lambda=-5.1):
        """
        For some beta schedules such as cosine schedule, the log-SNR has numerical isssues.
        DPM-Solver clip the log-SNR near t=T within -5.1 to ensure the stability.
        Such a trick is very useful for diffusion models with the cosine schedule, such as i-DDPM, guided-diffusion and GLIDE.
        """
        log_sigmas = 0.5 * torch.log(1. - torch.exp(2. * log_alphas))
        lambs = log_alphas - log_sigmas
        idx = torch.searchsorted(torch.flip(lambs, [0]), clipped_lambda)
        if idx > 0:
            log_alphas = log_alphas[:-idx]
        return log_alphas

    def marginal_log_mean_coeff(self, t):
        """
        Compute log(alpha_t) of a given continuous-time label t in [0, T].
        """
        if self.schedule == 'discrete':
            return interpolate_fn(t.reshape((-1, 1)), self.t_array.to(t.device),
                                  self.log_alpha_array.to(t.device)).reshape((-1))
        elif self.schedule == 'linear':
            return -0.25 * t ** 2 * (self.beta_1 - self.beta_0) - 0.5 * t * self.beta_0

    def marginal_alpha(self, t):
        """
        Compute alpha_t of a given continuous-time label t in [0, T].
        """
        return torch.exp(self.marginal_log_mean_coeff(t))

    def marginal_std(self, t):
        """
        Compute sigma_t of a given continuous-time label t in [0, T].
        """
        return torch.sqrt(1. - torch.exp(2. * self.marginal_log_mean_coeff(t)))

    def marginal_lambda(self, t):
        """
        Compute lambda_t = log(alpha_t) - log(sigma_t) of a given continuous-time label t in [0, T].
        """
        log_mean_coeff = self.marginal_log_mean_coeff(t)
        # print('log_mean_coeff=',log_mean_coeff)
        log_std = 0.5 * torch.log(1. - torch.exp(2. * log_mean_coeff))
        return log_mean_coeff - log_std

    def inverse_lambda(self, lamb):
        """
        Compute the continuous-time label t in [0, T] of a given half-logSNR lambda_t.
        """
        if self.schedule == 'linear':
            tmp = 2. * (self.beta_1 - self.beta_0) * torch.logaddexp(-2. * lamb, torch.zeros((1,)).to(lamb))
            Delta = self.beta_0 ** 2 + tmp
            return tmp / (torch.sqrt(Delta) + self.beta_0) / (self.beta_1 - self.beta_0)
        elif self.schedule == 'discrete':
            log_alpha = -0.5 * torch.logaddexp(torch.zeros((1,)).to(lamb.device), -2. * lamb)
            t = interpolate_fn(log_alpha.reshape((-1, 1)), torch.flip(self.log_alpha_array.to(lamb.device), [1]),
                               torch.flip(self.t_array.to(lamb.device), [1]))
            return t.reshape((-1,))

    def marginal_NSR(self, t):
        """
        Compute lambda_t = log(alpha_t) - log(sigma_t) of a given continuous-time label t in [0, T].
        """
        log_mean_coeff = self.marginal_log_mean_coeff(t)
        log_std = 0.5 * torch.log(1. - torch.exp(2. * log_mean_coeff))
        return torch.exp(log_std - log_mean_coeff)

    def inverse_NSR(self, nsr):
        """
        Compute the continuous-time label t in [0, T] of a given NSR.
        """
        if self.schedule == 'linear':
            tmp = 2. * (self.beta_1 - self.beta_0) * torch.log(1 + nsr ** 2).to(nsr)
            Delta = self.beta_0 ** 2 + tmp
            return tmp / (torch.sqrt(Delta) + self.beta_0) / (self.beta_1 - self.beta_0)
        elif self.schedule == 'discrete':
            log_alpha = -0.5 * torch.log(1 + nsr ** 2).to(nsr)
            t = interpolate_fn(log_alpha.reshape((-1, 1)), torch.flip(self.log_alpha_array.to(nsr.device), [1]),
                               torch.flip(self.t_array.to(nsr.device), [1]))
            return t.reshape((-1,))


def model_wrapper(
        model,
        noise_schedule,
        model_type="noise",
        model_kwargs={},
        guidance_type="uncond",
        condition=None,
        unconditional_condition=None,
        guidance_scale=1.,
        classifier_fn=None,
        classifier_kwargs={},
):
    def get_model_input_time(t_continuous):
        """
        Convert the continuous-time `t_continuous` (in [epsilon, T]) to the model input time.
        For discrete-time DPMs, we convert `t_continuous` in [1 / N, 1] to `t_input` in [0, 1000 * (N - 1) / N].
        For continuous-time DPMs, we just use `t_continuous`.
        """
        # print('t_continuous=',t_continuous)
        # print('(t_continuous - 1. / noise_schedule.total_N) * 1000=',(t_continuous - 1. / noise_schedule.total_N) * 1000)
        if noise_schedule.schedule == 'discrete':
            return (t_continuous - 1. / noise_schedule.total_N) * 1000.
        elif noise_schedule.schedule == 'linear':
            return 1000. * torch.max(t_continuous - 1. / noise_schedule.total_N,
                                     torch.zeros_like(t_continuous).to(t_continuous))
        else:
            return t_continuous

    def noise_pred_fn(x, t_continuous, cond=None):
        t_input = get_model_input_time(t_continuous)
        if cond is None:
            output = model(x, t_input, **model_kwargs)
        else:
            output = model(x, t_input, cond, **model_kwargs)
        if model_type == "noise":
            return output
        elif model_type == "x_start":
            alpha_t, sigma_t = noise_schedule.marginal_alpha(t_continuous), noise_schedule.marginal_std(t_continuous)
            return (x - alpha_t * output) / sigma_t
        elif model_type == "v":
            alpha_t, sigma_t = noise_schedule.marginal_alpha(t_continuous), noise_schedule.marginal_std(t_continuous)
            return alpha_t * output + sigma_t * x
        elif model_type == "score":
            sigma_t = noise_schedule.marginal_std(t_continuous)
            return -sigma_t * output

    def cond_grad_fn(x, t_input):
        """
        Compute the gradient of the classifier, i.e. nabla_{x} log p_t(cond | x_t).
        """
        with torch.enable_grad():
            x_in = x.detach().requires_grad_(True)
            log_prob = classifier_fn(x_in, t_input, condition, **classifier_kwargs)
            return torch.autograd.grad(log_prob.sum(), x_in)[0]

    def model_fn(x, t_continuous):
        """
        The noise predicition model function that is used for SciRE-Solver.
        """
        if guidance_type == "uncond":
            return noise_pred_fn(x, t_continuous)
        elif guidance_type == "classifier":
            assert classifier_fn is not None
            t_input = get_model_input_time(t_continuous)
            cond_grad = cond_grad_fn(x, t_input)
            sigma_t = noise_schedule.marginal_std(t_continuous)
            noise = noise_pred_fn(x, t_continuous)
            return noise - guidance_scale * expand_dims(sigma_t, x.dim()) * cond_grad
        elif guidance_type == "classifier-free":
            if guidance_scale == 1. or unconditional_condition is None:
                return noise_pred_fn(x, t_continuous, cond=condition)
            else:
                x_in = torch.cat([x] * 2)
                t_in = torch.cat([t_continuous] * 2)
                c_in = torch.cat([unconditional_condition, condition])
                noise_uncond, noise = noise_pred_fn(x_in, t_in, cond=c_in).chunk(2)
                return noise_uncond + guidance_scale * (noise - noise_uncond)

    assert model_type in ["noise", "x_start", "v", "score"]
    assert guidance_type in ["uncond", "classifier", "classifier-free"]
    return model_fn


class SciRE_Solver:
    def __init__(
            self,
            model_fn,
            noise_schedule,
            algorithm_type="scire",
            correcting_x0_fn=None,
            correcting_xt_fn=None,
            thresholding_max_val=1.,
            dynamic_thresholding_ratio=0.995,
    ):

        self.model = lambda x, t: model_fn(x, t.expand((x.shape[0])))
        self.noise_schedule = noise_schedule
        assert algorithm_type in ["scire", "dpm", "rde"]
        self.algorithm_type = algorithm_type
        if correcting_x0_fn == "dynamic_thresholding":
            self.correcting_x0_fn = self.dynamic_thresholding_fn
        else:
            self.correcting_x0_fn = correcting_x0_fn
        self.correcting_xt_fn = correcting_xt_fn
        self.dynamic_thresholding_ratio = dynamic_thresholding_ratio
        self.thresholding_max_val = thresholding_max_val

    def dynamic_thresholding_fn(self, x0, t):
        """
        The dynamic thresholding method.
        """
        dims = x0.dim()
        p = self.dynamic_thresholding_ratio
        s = torch.quantile(torch.abs(x0).reshape((x0.shape[0], -1)), p, dim=1)
        s = expand_dims(torch.maximum(s, self.thresholding_max_val * torch.ones_like(s).to(s.device)), dims)
        x0 = torch.clamp(x0, -s, s) / s
        return x0

    def noise_prediction_fn(self, x, t):
        """
        Return the noise prediction model.
        """
        return self.model(x, t)

    def data_prediction_fn(self, x, t):
        """
        Return the data prediction model (with corrector).
        """
        noise = self.noise_prediction_fn(x, t)
        alpha_t, sigma_t = self.noise_schedule.marginal_alpha(t), self.noise_schedule.marginal_std(t)
        x0 = (x - sigma_t * noise) / alpha_t
        if self.correcting_x0_fn is not None:
            x0 = self.correcting_x0_fn(x0, t)
        return x0

    def model_fn(self, x, t):
        """
        Convert the model to the noise prediction model or the data prediction model.
        """
        if self.algorithm_type == "dpmsolver++":
            return self.data_prediction_fn(x, t)
        else:
            return self.noise_prediction_fn(x, t)

    def get_time_steps(self, skip_type, t_T, t_0, N, device):
        """Compute the intermediate time steps for sampling."""
        if skip_type == 'NSR':
            NSR_T = self.noise_schedule.marginal_NSR(torch.tensor(t_T).to(device))
            NSR_0 = self.noise_schedule.marginal_NSR(torch.tensor(t_0).to(device))
            k = 3.1 #recommended to take (k\in[2,7])
            trans_T = -torch.log(NSR_T + k * NSR_0)
            trans_0 = -torch.log(NSR_0 + k * NSR_0)
            trans_steps = torch.linspace(trans_T.cpu().item(), trans_0.cpu().item(), N + 1).to(device)
            NSR_steps = torch.exp(-trans_steps)
            return self.noise_schedule.inverse_NSR(NSR_steps - k * NSR_0)
        elif skip_type == 'logSNR':
            lambda_T = self.noise_schedule.marginal_lambda(torch.tensor(t_T).to(device))
            lambda_0 = self.noise_schedule.marginal_lambda(torch.tensor(t_0).to(device))
            logSNR_steps = torch.linspace(lambda_T.cpu().item(), lambda_0.cpu().item(), N + 1).to(device)
            return self.noise_schedule.inverse_lambda(logSNR_steps)
        elif skip_type == 'time_uniform':
            return torch.linspace(t_T, t_0, N + 1).to(device)
        elif skip_type == 'time_quadratic':
            t_order = 2
            t = torch.linspace(t_T ** (1. / t_order), t_0 ** (1. / t_order), N + 1).pow(t_order).to(device)
            return t
        else:
            raise ValueError(
                "Unsupported skip_type {}, need to be 'NSR','logSNR' or 'time_uniform' or 'time_quadratic'".format(
                    skip_type))

    def get_orders_and_timesteps_for_singlestep_solver(self, steps, order, skip_type, t_T, t_0, device):
        """Get the order of each step for sampling by the singlestep SciRE-Solver."""
        if order == 3:
            K = steps // 3 + 1
            if steps % 3 == 0:
                orders = [3, ] * (K - 2) + [2, 1]
            elif steps % 3 == 1:
                orders = [3, ] * (K - 1) + [1]
            else:
                orders = [3, ] * (K - 1) + [2]
        elif order == 2:
            if steps % 2 == 0:
                K = steps // 2
                orders = [2, ] * K
            else:
                K = steps // 2 + 1
                orders = [2, ] * (K - 1) + [1]
        elif order == 1:
            K = 1
            orders = [1, ] * steps
        else:
            raise ValueError("'order' must be '1' or '2' or '3'.")
        if skip_type == 'logSNR':
            # To reproduce the results in SciRE-Solver paper
            timesteps_outer = self.get_time_steps(skip_type, t_T, t_0, K, device)
        else:
            timesteps_outer = self.get_time_steps(skip_type, t_T, t_0, steps, device)[
                torch.cumsum(torch.tensor([0, ] + orders), 0).to(device)]
        return timesteps_outer, orders

    def denoise_to_zero_fn(self, x, s):
        """
        Denoise at the final step, which is equivalent to solve the ODE from lambda_s to infty by first-order discretization.
        """
        return self.data_prediction_fn(x, s)

    def scire_solver_first_update(self, x, s, t, model_s=None, return_intermediate=False):
        """DDIM"""
        ns = self.noise_schedule
        dims = x.dim()
        NSR_s, NSR_t = ns.marginal_NSR(s), ns.marginal_NSR(t)
        log_alpha_s, log_alpha_t = ns.marginal_log_mean_coeff(s), ns.marginal_log_mean_coeff(t)
        alpha_t = torch.exp(log_alpha_t)
        if model_s is None:
            model_s = self.model_fn(x, s)
        x_t = (
                (torch.exp(log_alpha_t - log_alpha_s)) * x
                + (NSR_t - NSR_s) * expand_dims(alpha_t, dims) * model_s
        )
        if return_intermediate:
            return x_t, {'model_s': model_s}
        else:
            return x_t

    def singlestep_scire_solver_second_update(self, x, s, t, r1=0.5, model_s=None, return_intermediate=False,
                                              solver_type='scire'):
        #Here, we use phi_1(3) for RDE-based solver,
        # one can choose phi_1(m)=torch.exp(torch.tensor(1)) / torch.expm1(torch.tensor(1)) as described in our paper.
        if solver_type not in ['scire', 'ei']:
            raise ValueError("'solver_type' must be 'scire' or 'ei', got {}".format(solver_type))
        if r1 is None:
            r1 = 0.5
        ns = self.noise_schedule
        dims = len(x.shape) - 1
        if solver_type == "scire":
            NSR_s, NSR_t = ns.marginal_NSR(s), ns.marginal_NSR(t)
            h = NSR_t - NSR_s
            NSR_s1 = NSR_s + r1 * h
            s1 = ns.inverse_NSR(NSR_s1)
            log_alpha_s, log_alpha_s1, log_alpha_t = ns.marginal_log_mean_coeff(s), ns.marginal_log_mean_coeff(
                s1), ns.marginal_log_mean_coeff(t)
            alpha_s1, alpha_t, alpha_s = torch.exp(log_alpha_s1), torch.exp(log_alpha_t), torch.exp(log_alpha_s)
            if model_s is None:
                model_s = self.model_fn(x, s)
            x_s1 = (
                    (torch.exp(log_alpha_s1 - log_alpha_s)) * x
                    + (NSR_s1 - NSR_s) * expand_dims(alpha_s1, dims) * model_s
            )
            model_s1 = self.model_fn(x_s1, s1)
            if self.algorithm_type == 'rde':#Recursive difference estimation
                x_t = (
                        (torch.exp(log_alpha_t - log_alpha_s)) * x
                        + (NSR_t - NSR_s) * expand_dims(alpha_t, dims) * model_s
                        + 3 / 4 * (NSR_t - NSR_s) ** 2 / (NSR_s1 - NSR_s) * expand_dims(alpha_t, dims) * (model_s1 - model_s)
                )
            elif self.algorithm_type == 'fde':#Finite difference estimation
                x_t = (
                        (torch.exp(log_alpha_t - log_alpha_s)) * x
                        + (NSR_t - NSR_s) * expand_dims(alpha_t, dims) * model_s
                        +0.5*(NSR_t-NSR_s)**2/(NSR_s1-NSR_s)*expand_dims(alpha_t, dims)*(model_s1 - model_s) #FDE-based
                    )
            else:
                raise ValueError("'algorithm_type' must be 'rde' or 'fde', got {}".format(algorithm_type))

        elif solver_type == "ei":
            lambda_s, lambda_t = ns.marginal_lambda(s), ns.marginal_lambda(t)
            h = lambda_t - lambda_s
            lambda_s1 = lambda_s + r1 * h
            s1 = ns.inverse_lambda(lambda_s1)
            log_alpha_s, log_alpha_s1, log_alpha_t = ns.marginal_log_mean_coeff(s), ns.marginal_log_mean_coeff(
                s1), ns.marginal_log_mean_coeff(t)
            sigma_s, sigma_s1, sigma_t = ns.marginal_std(s), ns.marginal_std(s1), ns.marginal_std(t)
            alpha_s1, alpha_t = torch.exp(log_alpha_s1), torch.exp(log_alpha_t)
            phi_11 = torch.expm1(r1 * h)
            phi_1 = torch.expm1(h)

            if model_s is None:
                model_s = self.model_fn(x, s)
            x_s1 = (
                    torch.exp(log_alpha_s1 - log_alpha_s) * x
                    - (sigma_s1 * phi_11) * model_s
            )
            model_s1 = self.model_fn(x_s1, s1)
            if self.algorithm_type == 'rde': #EIRE: An Exponential Integrator with RDE proposed by our paper.
                x_t = (
                        torch.exp(log_alpha_t - log_alpha_s) * x
                        - (sigma_t * phi_1) * model_s
                        - (3 / h) * (sigma_t * (phi_1 - h)) * (model_s1 - model_s)
                )
            elif self.algorithm_type == 'dpm':# DPM-Solver, our baseline
                x_t = (
                        torch.exp(log_alpha_t - log_alpha_s) * x
                        - (sigma_t * phi_1) * model_s
                        - (0.5 / r1) * (sigma_t * phi_1) * (model_s1 - model_s)
                )
            else:
                raise ValueError("'algorithm_type' must be 'rde' or 'dpm', got {}".format(algorithm_type))
        else:
            raise ValueError("'solver_type' must be 'scire' or 'ei', got {}".format(solver_type))
        if return_intermediate:
            return x_t, {'model_s': model_s, 'model_s1': model_s1}
        else:
            return x_t

    def singlestep_scire_solver_third_update(self, x, s, t, r1=1. / 3., r2=2. / 3., model_s=None, model_s1=None,
                                             return_intermediate=False, solver_type='scire'):
        if solver_type not in ['scire', 'ei']:
            raise ValueError("'solver_type' must be either 'scire' or 'ei', got {}".format(solver_type))
        if r1 is None:
            r1 = 1. / 3.
        if r2 is None:
            r2 = 2. / 3.
        ns = self.noise_schedule
        dims = len(x.shape) - 1
        if self.solver_type == "scire":
            NSR_s, NSR_t = ns.marginal_NSR(s), ns.marginal_NSR(t)
            h = NSR_t - NSR_s
            NSR_s1 = NSR_s + r1 * h
            NSR_s2 = NSR_s + r2 * h
            s1 = ns.inverse_NSR(NSR_s1)
            s2 = ns.inverse_NSR(NSR_s2)

            log_alpha_s, log_alpha_s1, log_alpha_s2, log_alpha_t = ns.marginal_log_mean_coeff(
                s), ns.marginal_log_mean_coeff(s1), ns.marginal_log_mean_coeff(s2), ns.marginal_log_mean_coeff(t)
            alpha_s, alpha_s1, alpha_s2, alpha_t = torch.exp(log_alpha_s), torch.exp(log_alpha_s1), torch.exp(
                log_alpha_s2), torch.exp(log_alpha_t)
            sigma_s1, sigma_s2, sigma_t = ns.marginal_std(s1), ns.marginal_std(s2), ns.marginal_std(t)
            # ee=torch.exp(torch.tensor(1))/torch.expm1(torch.tensor(1))
            if model_s is None:
                model_s = self.model_fn(x, s)
            if model_s1 is None:
                x_s1 = (
                        torch.exp(log_alpha_s1 - log_alpha_s)[(...,) + (None,) * dims] * x
                        + ((NSR_s1 - NSR_s) * alpha_s1)[(...,) + (None,) * dims] * model_s
                )
                model_s1 = self.model_fn(x_s1, s1)
            if algorithm_type == 'rde':
                x_s2 = (
                    torch.exp(log_alpha_s2 - log_alpha_s)[(...,) + (None,) * dims] * x
                    + ((NSR_s2 - NSR_s) * alpha_s2)[(...,) + (None,) * dims] * model_s
                    + (0.5 / r1 * (NSR_t - NSR_s) * alpha_s2)[(...,) + (None,) * dims] * (model_s1 - model_s)
                )
                model_s2 = self.model_fn(x_s2, s2)
                x_t = (
                    torch.exp(log_alpha_t - log_alpha_s)[(...,) + (None,) * dims] * x
                    + ((NSR_t - NSR_s) * alpha_t)[(...,) + (None,) * dims] * model_s
                    + (3 / 4 / r2 * (NSR_t - NSR_s) * alpha_t)[(...,) + (None,) * dims] * (model_s2 - model_s)
                )
            else:
                raise ValueError("'algorithm_type' must be 'rde', got {}".format(algorithm_type))
        elif self.solver_type == "ei":
            lambda_s, lambda_t = ns.marginal_lambda(s), ns.marginal_lambda(t)
            h = lambda_t - lambda_s
            lambda_s1 = lambda_s + r1 * h
            lambda_s2 = lambda_s + r2 * h
            s1 = ns.inverse_lambda(lambda_s1)
            s2 = ns.inverse_lambda(lambda_s2)
            log_alpha_s, log_alpha_s1, log_alpha_s2, log_alpha_t = ns.marginal_log_mean_coeff(
                s), ns.marginal_log_mean_coeff(s1), ns.marginal_log_mean_coeff(s2), ns.marginal_log_mean_coeff(t)
            sigma_s, sigma_s1, sigma_s2, sigma_t = ns.marginal_std(s), ns.marginal_std(s1), ns.marginal_std(
                s2), ns.marginal_std(t)
            alpha_s1, alpha_s2, alpha_t = torch.exp(log_alpha_s1), torch.exp(log_alpha_s2), torch.exp(log_alpha_t)
            phi_11 = torch.expm1(r1 * h)
            phi_12 = torch.expm1(r2 * h)
            phi_1 = torch.expm1(h)
            phi_22 = torch.expm1(r2 * h) / (r2 * h) - 1.
            phi_2 = phi_1 / h - 1.
            phi_3 = phi_2 / h - 0.5

            if model_s is None:
                model_s = self.model_fn(x, s)
            if model_s1 is None:
                x_s1 = (
                        (torch.exp(log_alpha_s1 - log_alpha_s)) * x
                        - (sigma_s1 * phi_11) * model_s
                )
                model_s1 = self.model_fn(x_s1, s1)
            if algorithm_type == 'dpm':
                x_s2 = (
                    (torch.exp(log_alpha_s2 - log_alpha_s)) * x
                    - (sigma_s2 * phi_12) * model_s
                    - r2 / r1 * (sigma_s2 * phi_22) * (model_s1 - model_s)
                )
                model_s2 = self.model_fn(x_s2, s2)
                x_t = (
                        (torch.exp(log_alpha_t - log_alpha_s)) * x
                        - (sigma_t * phi_1) * model_s
                        - (1. / r2) * (sigma_t * phi_2) * (model_s2 - model_s)
                )
            else:
                raise ValueError("'algorithm_type' must be 'dpm', got {}".format(algorithm_type))
        else:
            raise ValueError("'solver_type' must be 'scire' or 'ei', got {}".format(solver_type))
        if return_intermediate:
            return x_t, {'model_s': model_s, 'model_s1': model_s1, 'model_s2': model_s2}
        else:
            return x_t

    def singlestep_scire_solver_update(self, x, s, t, order, return_intermediate=False, solver_type='scire', r1=None,
                                       r2=None):
        """ Singlestep SciRE-Solver with the order `order` from time `s` to time `t`."""
        if order == 1:
            return self.scire_solver_first_update(x, s, t, return_intermediate=return_intermediate)
        elif order == 2:
            return self.singlestep_scire_solver_second_update(x, s, t, return_intermediate=return_intermediate,
                                                              solver_type=solver_type, r1=r1)
        elif order == 3:
            return self.singlestep_scire_solver_third_update(x, s, t, return_intermediate=return_intermediate,
                                                             solver_type=solver_type, r1=r1, r2=r2)
        else:
            raise ValueError("Solver order must be 1 or 2 or 3, got {}".format(order))

    def add_noise(self, x, t, noise=None):
        """
        Compute the noised input xt = alpha_t * x + sigma_t * noise.

        Args:
            x: A `torch.Tensor` with shape `(batch_size, *shape)`.
            t: A `torch.Tensor` with shape `(t_size,)`.
        Returns:
            xt with shape `(t_size, batch_size, *shape)`.
        """
        alpha_t, sigma_t = self.noise_schedule.marginal_alpha(t), self.noise_schedule.marginal_std(t)
        if noise is None:
            noise = torch.randn((t.shape[0], *x.shape), device=x.device)
        x = x.reshape((-1, *x.shape))
        xt = expand_dims(alpha_t, x.dim()) * x + expand_dims(sigma_t, x.dim()) * noise
        if t.shape[0] == 1:
            return xt.squeeze(0)
        else:
            return xt

    def inverse(self, x, steps=20, t_start=None, t_end=None, order=2, skip_type='time_uniform',
                method='multistep', lower_order_final=True, denoise_to_zero=False, solver_type='scire',
                atol=0.0078, rtol=0.05, return_intermediate=False,
                ):
        """
        Inverse the sample `x` from time `t_start` to `t_end` by SciRE-Solver.
        For discrete-time DPMs, we use `t_start=1/N`, where `N` is the total time steps during training.
        """
        t_0 = 1. / self.noise_schedule.total_N if t_start is None else t_start
        t_T = self.noise_schedule.T if t_end is None else t_end
        assert t_0 > 0 and t_T > 0, "Time range needs to be greater than 0. For discrete-time DPMs, it needs to be in [1 / N, 1], where N is the length of betas array"
        return self.sample(x, steps=steps, t_start=t_0, t_end=t_T, order=order, skip_type=skip_type,
                           method=method, lower_order_final=lower_order_final, denoise_to_zero=denoise_to_zero,
                           solver_type=solver_type,
                           atol=atol, rtol=rtol, return_intermediate=return_intermediate)

    def sample(self, x, steps=20, t_start=None, t_end=1e-4, order=2, skip_type='time_uniform',
               method='multistep', lower_order_final=True, denoise_to_zero=False, solver_type='scire',
               atol=0.0078, rtol=0.05, return_intermediate=False,
               ):

        if self.noise_schedule.schedule == 'discrete':
            t_0 = 1. / self.noise_schedule.total_N if t_end is None else t_end
            t_T = self.noise_schedule.T if t_start is None else t_start
        elif self.noise_schedule.schedule == 'linear':
            t_0 = t_end
            t_T = self.noise_schedule.T if t_start is None else t_start
        assert t_0 > 0 and t_T > 0, "Time range needs to be greater than 0. For discrete-time DPMs, it needs to be in [1 / N, 1], where N is the length of betas array"
        if return_intermediate:
            assert method in ['multistep', 'singlestep',
                              'singlestep_fixed'], "Cannot use adaptive solver when saving intermediate values"
        if self.correcting_xt_fn is not None:
            assert method in ['multistep', 'singlestep',
                              'singlestep_fixed'], "Cannot use adaptive solver when correcting_xt_fn is not None"
        device = x.device
        intermediates = []
        with torch.no_grad():
            if method == 'adaptive':
                x = self.scire_solver_adaptive(x, order=order, t_T=t_T, t_0=t_0, atol=atol, rtol=rtol,
                                               solver_type=solver_type)
            elif method == 'multistep':
                assert steps >= order
                timesteps = self.get_time_steps(skip_type=skip_type, t_T=t_T, t_0=t_0, N=steps, device=device)
                assert timesteps.shape[0] - 1 == steps
                # Init the initial values.
                step = 0
                t = timesteps[step]
                t_prev_list = [t]
                model_prev_list = [self.model_fn(x, t)]
                if self.correcting_xt_fn is not None:
                    x = self.correcting_xt_fn(x, t, step)
                if return_intermediate:
                    intermediates.append(x)
                # Init the first `order` values by lower order multistep SciRE-Solver.
                for step in range(1, order):
                    t = timesteps[step]
                    x = self.multistep_scire_solver_update(x, model_prev_list, t_prev_list, t, step,
                                                           solver_type=solver_type)
                    if self.correcting_xt_fn is not None:
                        x = self.correcting_xt_fn(x, t, step)
                    if return_intermediate:
                        intermediates.append(x)
                    t_prev_list.append(t)
                    model_prev_list.append(self.model_fn(x, t))
                # Compute the remaining values by `order`-th order multistep SciRE-Solver.
                for step in range(order, steps + 1):
                    t = timesteps[step]
                    # We only use lower order for steps < 10
                    if lower_order_final and steps < 10:
                        step_order = min(order, steps + 1 - step)
                    else:
                        step_order = order
                    x = self.multistep_scire_solver_update(x, model_prev_list, t_prev_list, t, step_order,
                                                           solver_type=solver_type)
                    if self.correcting_xt_fn is not None:
                        x = self.correcting_xt_fn(x, t, step)
                    if return_intermediate:
                        intermediates.append(x)
                    for i in range(order - 1):
                        t_prev_list[i] = t_prev_list[i + 1]
                        model_prev_list[i] = model_prev_list[i + 1]
                    t_prev_list[-1] = t
                    # We do not need to evaluate the final model value.
                    if step < steps:
                        model_prev_list[-1] = self.model_fn(x, t)
            elif method in ['singlestep', 'singlestep_fixed']:
                if method == 'singlestep':
                    timesteps_outer, orders = self.get_orders_and_timesteps_for_singlestep_solver(steps=steps,
                                                                                                  order=order,
                                                                                                  skip_type=skip_type,
                                                                                                  t_T=t_T, t_0=t_0,
                                                                                                  device=device)
                    # print('timesteps_outer=',timesteps_outer,'t_0=',t_0,'t_T=',t_T)
                elif method == 'singlestep_fixed':
                    K = steps // order
                    orders = [order, ] * K
                    timesteps_outer = self.get_time_steps(skip_type=skip_type, t_T=t_T, t_0=t_0, N=K, device=device)
                    # print('timesteps_outer=',timesteps_outer)
                for step, order in enumerate(orders):
                    s, t = timesteps_outer[step], timesteps_outer[step + 1]
                    x = self.singlestep_scire_solver_update(x, s, t, order, solver_type=solver_type)
                    if self.correcting_xt_fn is not None:
                        x = self.correcting_xt_fn(x, t, step)
                    if return_intermediate:
                        intermediates.append(x)
            else:
                raise ValueError("Got wrong method {}".format(method))
            if denoise_to_zero:
                t = torch.ones((1,)).to(device) * t_0
                x = self.denoise_to_zero_fn(x, t)
                if self.correcting_xt_fn is not None:
                    x = self.correcting_xt_fn(x, t, step + 1)
                if return_intermediate:
                    intermediates.append(x)
        if return_intermediate:
            return x, intermediates
        else:
            return x


#############################################################
# other utility functions
#############################################################

def interpolate_fn(x, xp, yp):
    """
    A piecewise linear function y = f(x), using xp and yp as keypoints.
    We implement f(x) in a differentiable way (i.e. applicable for autograd).
    The function f(x) is well-defined for all x-axis. (For x beyond the bounds of xp, we use the outmost points of xp to define the linear function.)

    Args:
        x: PyTorch tensor with shape [N, C], where N is the batch size, C is the number of channels (we use C = 1 for SciRE-Solver).
        xp: PyTorch tensor with shape [C, K], where K is the number of keypoints.
        yp: PyTorch tensor with shape [C, K].
    Returns:
        The function values f(x), with shape [N, C].
    """
    N, K = x.shape[0], xp.shape[1]
    all_x = torch.cat([x.unsqueeze(2), xp.unsqueeze(0).repeat((N, 1, 1))], dim=2)
    sorted_all_x, x_indices = torch.sort(all_x, dim=2)
    x_idx = torch.argmin(x_indices, dim=2)
    cand_start_idx = x_idx - 1
    start_idx = torch.where(
        torch.eq(x_idx, 0),
        torch.tensor(1, device=x.device),
        torch.where(
            torch.eq(x_idx, K), torch.tensor(K - 2, device=x.device), cand_start_idx,
        ),
    )
    end_idx = torch.where(torch.eq(start_idx, cand_start_idx), start_idx + 2, start_idx + 1)
    start_x = torch.gather(sorted_all_x, dim=2, index=start_idx.unsqueeze(2)).squeeze(2)
    end_x = torch.gather(sorted_all_x, dim=2, index=end_idx.unsqueeze(2)).squeeze(2)
    start_idx2 = torch.where(
        torch.eq(x_idx, 0),
        torch.tensor(0, device=x.device),
        torch.where(
            torch.eq(x_idx, K), torch.tensor(K - 2, device=x.device), cand_start_idx,
        ),
    )
    y_positions_expanded = yp.unsqueeze(0).expand(N, -1, -1)
    start_y = torch.gather(y_positions_expanded, dim=2, index=start_idx2.unsqueeze(2)).squeeze(2)
    end_y = torch.gather(y_positions_expanded, dim=2, index=(start_idx2 + 1).unsqueeze(2)).squeeze(2)
    cand = start_y + (x - start_x) * (end_y - start_y) / (end_x - start_x)
    return cand


def expand_dims(v, dims):
    """
    Expand the tensor `v` to the dim `dims`.

    Args:
        `v`: a PyTorch tensor with shape [N].
        `dim`: a `int`.
    Returns:
        a PyTorch tensor with shape [N, 1, 1, ..., 1] and the total dimension is `dims`.
    """
    return v[(...,) + (None,) * (dims - 1)]