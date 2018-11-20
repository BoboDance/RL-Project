import autograd.numpy as np
from autograd import value_and_grad
from scipy.optimize import minimize

from PILCO.Kernels.Kernels import RBFKernel, WhiteNoiseKernel


class GaussianProcess(object):
    """
    Gaussian Process Regression
    """

    def __init__(self, length_scales, sigma_f=1, sigma_eps=0.01):
        """

        :param optimizer:
        :param sigma_f:
        :param sigma_eps:
        :param alpha:
        :param n_restarts_optimizer:
        :param normalize_y:
        :param copy_X_train:
        :param random_state:
        """
        self.kernel = RBFKernel() + WhiteNoiseKernel()

        self.X = None
        self.y = None
        self.sigma_eps = sigma_eps
        self.length_scales = length_scales
        self.sigma_f = sigma_f

        self.n_targets = None
        self.state_dim = None

        # params to penalize high hyperparams
        self.length_scale_pen = 100
        self.signal_to_noise = 500
        self.std_pen = 1

        self.betas = None
        self.K_inv = None
        self.qs = None

    def fit(self, X, y):
        # ensure they are 2d
        y = y.reshape(y.shape[0], -1)
        X = X.reshape(X.shape[0], -1)

        self.X = X
        self.y = y

        self.n_targets = y.shape[1]
        self.state_dim = X.shape[1]

        self.std_pen = np.std(X, 0)
        self.optimize()

    def _optimize_hyperparams(self, params, *args):
        p = 30
        length_scales, sigma_f, sigma_eps = self._unwrap_params(params)

        L = self.log_marginal_likelihood(params)
        L = L + np.sum(((length_scales - np.log(self.std_pen)) / np.log(self.length_scale_pen)) ** p)
        L = L + np.sum(((sigma_f - sigma_eps) / np.log(self.signal_to_noise)) ** p)
        print(L)
        return L

    def optimize(self):
        """
        This is used to optimize the hyperparams for the GP, given it is not the policy
        :return:
        """

        params = self._wrap_kernel_hyperparams()

        try:
            res = minimize(value_and_grad(self._optimize_hyperparams), params, jac=True, method='L-BFGS-B')
        except Exception:
            res = minimize(value_and_grad(self._optimize_hyperparams), params, jac=True, method='CG')

        best_params = res.x

        l, f, e = self._unwrap_params(best_params)
        self.length_scales, self.sigma_f, self.sigma_eps = np.exp(l), np.exp(f), np.exp(e)
        self.compute_params()

    def _wrap_kernel_hyperparams(self):

        split1 = self.n_targets * self.state_dim
        split2 = self.n_targets + split1

        params = np.zeros([self.n_targets, self.state_dim + 2])
        params[:, :split1] = np.log(self.length_scales)
        params[:, split1:split2] = np.log(self.sigma_f)
        params[:, split2:] = np.log(self.sigma_eps)

        return params

    def log_marginal_likelihood(self, hyp):

        K = self.kernel(hyp, self.X)[0]  # [n, n]
        L = np.linalg.cholesky(K)
        alpha = np.linalg.solve(K, self.y)

        return 0.5 * self.X.shape[0] * self.n_targets * np.log(2 * np.pi) + 0.5 * np.dot(self.y.flatten(), alpha) + \
               np.sum(np.log(np.diag(L)))

    def compute_params(self):

        params = self._wrap_kernel_hyperparams()
        K = self.kernel(params, self.X)[0]

        # learned variance from evidence maximization
        noise = np.identity(self.X.shape[0]) * self.sigma_eps
        self.K_inv = np.linalg.solve(K + noise, np.identity(K.shape[0]))
        self.betas = K @ self.y

        # -------------------------------
        # TODO: Prob better, but autograd has no solve_triangular
        # L = np.linalg.cholesky(K + noise)
        # self.K_inv = solve_triangular(L, np.identity(self.X.shape[0]))
        # self.betas = solve_triangular(L, self.y)

    def compute_mu(self, mu, sigma):
        """
        Returns the new mean value of the predictive dist, e.g. p(u), given x~N(mu, sigma) via Moment matching
        :param mu: mean of input, e.g. state-action distribution
        :param sigma: covar of the input, e.g joint state-action distribution to represent uncertainty
        :return: mu of predictive distribution p(u)
        """

        # Numerically more stable???
        precision = np.diag(self.length_scales)

        diff = (self.X - mu) @ precision

        # TODO: This is going to give nan for negative det
        B = precision @ sigma @ precision + np.identity(precision.shape[0])
        t = diff @ B

        coefficient = 2 * self.sigma_f * np.linalg.det(B) ** -.5
        mean = coefficient * self.betas.T @ np.exp(-.5 * np.sum(diff * t, 1))

        return mean

    def _unwrap_params(self, params):

        split1 = self.state_dim * self.n_targets
        split2 = self.n_targets + split1

        length_scales = params[:split1].reshape(self.n_targets, self.state_dim)
        sigma_f = params[split1:split2]
        sigma_eps = params[split2:]
        return length_scales, sigma_f, sigma_eps

    # @property
    # def length_scales(self):
    #     return self.kernel.get_params()['k1__k2__length_scale']
    #
    # @property
    # def sigma_f(self):
    #     return self.kernel.get_params()['k1__k1__constant_value']
    #
    # @property
    # def sigma_eps(self):
    #     return self.kernel.get_params()['k2__noise_level']
    #
    # @length_scales.setter
    # def length_scales(self, length_scales: np.ndarray) -> None:
    #     self.kernel.set_params(k1__k2__length_scale=length_scales)
    #
    # @sigma_eps.setter
    # def sigma_eps(self, value):
    #     self._sigma_eps = value
    #
    # @sigma_f.setter
    # def sigma_f(self, value):
    #     self._sigma_f = value