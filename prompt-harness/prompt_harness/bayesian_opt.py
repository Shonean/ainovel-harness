"""贝叶斯优化（轻量实现，仅依赖 numpy + scipy）。

高斯过程 (GP) 回归作为代理模型，
期望改进 (Expected Improvement, EI) 作为采集函数。

不用 scikit-optimize，避免额外依赖。
用 scipy.stats.multivariate_normal 做 GP 推断，
用 scipy.optimize 做 EI 最大化。

Matérn 5/2 核函数手撸。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize
from scipy.spatial.distance import cdist


# ── 核函数 ────────────────────────────────────────────────

def _matern52_kernel(X1: np.ndarray, X2: np.ndarray, length_scale: float, signal_var: float) -> np.ndarray:
    """Matérn 5/2 核函数。

    k(x1, x2) = σ² * (1 + √5·d/ℓ + 5·d²/(3ℓ²)) · exp(-√5·d/ℓ)
    其中 d = ||x1 - x2||
    """
    # X1: (n1, d), X2: (n2, d) → dists: (n1, n2)
    dists = cdist(X1, X2, metric="euclidean")
    sqrt5 = np.sqrt(5.0)
    ratio = sqrt5 * dists / length_scale
    return signal_var * (1.0 + ratio + ratio**2 / 3.0) * np.exp(-ratio)


# ── GP 代理模型 ───────────────────────────────────────────

class GaussianProcessRegressor:
    """轻量 GP 回归器。

    超参数（length_scale, signal_var, noise_var）通过最大似然估计。
    为了性能，只在观测点数变化时或每隔几轮重新拟合一次。
    """

    def __init__(self, length_scale: float = 0.2, signal_var: float = 1.0, noise_var: float = 1e-4):
        self.length_scale = length_scale
        self.signal_var = signal_var
        self.noise_var = noise_var
        self.X_train_: np.ndarray | None = None
        self.y_train_: np.ndarray | None = None
        self.K_inv_: np.ndarray | None = None
        self.L_: np.ndarray | None = None
        self.alpha_: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray, *, optimize: bool = True) -> "GaussianProcessRegressor":
        """拟合 GP。

        Args:
            X: (n, d) 观测点
            y: (n,) 观测值
            optimize: 是否优化超参数（MLE）
        """
        self.X_train_ = np.asarray(X, dtype=float)
        self.y_train_ = np.asarray(y, dtype=float).ravel()

        if optimize and len(y) >= 3:
            self._optimize_hyperparams()

        # 计算 L = cholesky(K + σ²I), α = K⁻¹y
        K = _matern52_kernel(self.X_train_, self.X_train_, self.length_scale, self.signal_var)
        K += self.noise_var * np.eye(len(K))
        self.L_ = np.linalg.cholesky(K)
        self.alpha_ = np.linalg.solve(self.L_.T, np.linalg.solve(self.L_, self.y_train_))
        return self

    def predict(self, X: np.ndarray, return_std: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
        """预测。

        Returns:
            (mean, std) — mean: (n,), std: (n,) 或 None
        """
        assert self.X_train_ is not None and self.alpha_ is not None, "GP not fitted"
        X = np.asarray(X, dtype=float)

        K_s = _matern52_kernel(self.X_train_, X, self.length_scale, self.signal_var)  # (n_train, n_test)
        mu = K_s.T @ self.alpha_  # (n_test,)

        if not return_std:
            return mu, None

        # v = L⁻¹ K_s
        v = np.linalg.solve(self.L_, K_s)  # (n_train, n_test)
        K_ss_diag = self.signal_var * np.ones(len(X))  # 先验方差
        var = K_ss_diag - np.sum(v**2, axis=0)  # (n_test,)
        var = np.maximum(var, 1e-10)  # 数值稳定
        std = np.sqrt(var)
        return mu, std

    def _optimize_hyperparams(self) -> None:
        """用对数边际似然优化超参数。

        对 log(length_scale) 和 log(signal_var) 做网格 + 局部优化。
        维度不高，够用了。
        """
        n = len(self.y_train_)
        y = self.y_train_

        def neg_log_marginal_likelihood(log_params: np.ndarray) -> float:
            ls = np.exp(log_params[0])
            sv = np.exp(log_params[1])
            K = _matern52_kernel(self.X_train_, self.X_train_, ls, sv)
            K += self.noise_var * np.eye(n)
            try:
                L = np.linalg.cholesky(K)
            except np.linalg.LinAlgError:
                return 1e10
            # α = L⁻¹ y
            alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
            # log p(y) = -½ yᵀα - Σ log L_ii - n/2 log(2π)
            log_det = 2.0 * np.sum(np.log(np.diag(L)))
            nll = 0.5 * y @ alpha + 0.5 * log_det + 0.5 * n * np.log(2 * np.pi)
            return float(nll)

        # 网格搜索初值
        best_nll = float("inf")
        best_ls = self.length_scale
        best_sv = self.signal_var
        for log_ls in np.linspace(np.log(0.05), np.log(0.8), 8):
            for log_sv in np.linspace(np.log(0.01), np.log(2.0), 6):
                nll = neg_log_marginal_likelihood(np.array([log_ls, log_sv]))
                if nll < best_nll:
                    best_nll = nll
                    best_ls = np.exp(log_ls)
                    best_sv = np.exp(log_sv)

        # 局部精修
        try:
            res = minimize(
                neg_log_marginal_likelihood,
                x0=np.array([np.log(best_ls), np.log(best_sv)]),
                method="L-BFGS-B",
                bounds=[(np.log(0.01), np.log(2.0)), (np.log(0.001), np.log(5.0))],
            )
            if res.fun < best_nll:
                best_ls = np.exp(res.x[0])
                best_sv = np.exp(res.x[1])
        except Exception:
            pass

        self.length_scale = best_ls
        self.signal_var = best_sv


# ── 采集函数 ──────────────────────────────────────────────

def _expected_improvement(
    X: np.ndarray,
    gp: GaussianProcessRegressor,
    best_so_far: float,
    xi: float = 0.01,
) -> np.ndarray:
    """EI 采集函数。

    EI(x) = (μ(x) - f⁺ - ξ) · Φ(·) + σ(x) · φ(·)
    其中 Φ 是正态 CDF，φ 是正态 PDF。
    """
    mu, std = gp.predict(X, return_std=True)
    if std is None:
        return np.zeros(len(X))

    improvement = mu - best_so_far - xi
    Z = improvement / np.maximum(std, 1e-10)
    ei = improvement * norm.cdf(Z) + std * norm.pdf(Z)
    ei[std < 1e-10] = 0.0
    return ei


# ── 贝叶斯优化器 ──────────────────────────────────────────

class PromptBayesianOptimizer:
    """Prompt P 空间贝叶斯优化器。

    P ∈ [0, 1]^D，D 维连续空间。
    目标函数 f(P) → [0, 1]，越大越好。

    用法：
        opt = PromptBayesianOptimizer(dim=40)   # dim = P_DIM_META["dim"]
        # 先加初始观测
        for p, s in initial_points:
            opt.observe(p, s)
        # 循环
        for _ in range(n_iter):
            p_next, info = opt.suggest_next()
            score = evaluate(p_next)   # 正向生成 + 计算综合分
            opt.observe(p_next, score)
        best_p, best_score = opt.get_best()
    """

    def __init__(self, dim: int, bounds: list[tuple[float, float]] | None = None, acq_func: str = "ei"):
        self.dim = dim
        self.bounds = bounds or [(0.0, 1.0)] * dim
        self.acq_func = acq_func

        self.X_obs: list[np.ndarray] = []
        self.y_obs: list[float] = []
        self.gp = GaussianProcessRegressor()
        self._fitted = False

    # ── 观测 ──

    def observe(self, p_vector: list[float] | np.ndarray, score: float) -> None:
        """加入一个观测点。"""
        p = np.asarray(p_vector, dtype=float).ravel()
        assert len(p) == self.dim, f"维度不匹配: {len(p)} vs {self.dim}"
        # 裁剪到 [0, 1]
        p = np.clip(p, 0.0, 1.0)
        self.X_obs.append(p)
        self.y_obs.append(float(score))
        self._fitted = False

    # ── 建议 ──

    def suggest_next(self, *, n_restarts: int = 10) -> tuple[list[float], dict]:
        """建议下一个评估点。

        用多起点 L-BFGS-B 最大化 EI。
        加随机探索，避免局部最优。

        Returns:
            (p_next_vector, info_dict)
        """
        n_obs = len(self.y_obs)

        # 数据太少 → 随机探索
        if n_obs < 2:
            return self._random_sample(), {"method": "random_exploration", "ei": 0.0}

        self._refit_gp()

        best_y = max(self.y_obs)

        # 多起点优化 EI
        best_ei = -1.0
        best_x = None

        # 起点：从已有观测附近扰动 + 完全随机
        starts = []
        # 已有观测的扰动
        for x_obs in self.X_obs[:5]:
            noise = np.random.normal(0, 0.1, size=self.dim)
            x0 = np.clip(x_obs + noise, 0.0, 1.0)
            starts.append(x0)
        # 随机起点
        for _ in range(n_restarts - len(starts)):
            starts.append(np.random.rand(self.dim))

        def neg_ei(x: np.ndarray) -> float:
            ei_val = _expected_improvement(x.reshape(1, -1), self.gp, best_y)
            return -float(ei_val[0])

        bounds = self.bounds
        for x0 in starts:
            try:
                res = minimize(neg_ei, x0=x0, method="L-BFGS-B", bounds=bounds)
                ei_val = -res.fun
                if ei_val > best_ei:
                    best_ei = ei_val
                    best_x = res.x
            except Exception:
                continue

        # 每次都有一定概率纯随机探索（平衡探索-利用）
        explore_prob = max(0.05, 1.0 / (n_obs + 1))
        if np.random.random() < explore_prob or best_x is None:
            return self._random_sample(), {"method": "random_exploration", "ei": best_ei}

        return best_x.tolist(), {"method": "ei", "ei": round(best_ei, 6)}

    # ── 查询 ──

    def get_best(self) -> tuple[list[float], float]:
        """返回当前最优 (p_vector, score)。"""
        if not self.y_obs:
            return [0.5] * self.dim, 0.0
        idx = int(np.argmax(self.y_obs))
        return self.X_obs[idx].tolist(), self.y_obs[idx]

    def get_convergence_curve(self) -> list[float]:
        """返回按观测顺序的 best-so-far 曲线。"""
        curve = []
        best = -float("inf")
        for y in self.y_obs:
            best = max(best, y)
            curve.append(best)
        return curve

    def n_observations(self) -> int:
        return len(self.y_obs)

    # ── 内部 ──

    def _refit_gp(self) -> None:
        X = np.array(self.X_obs)
        y = np.array(self.y_obs)
        # 每 5 轮或观测数变化时重新拟合超参数
        optimize = (len(y) % 5 == 0) or (not self._fitted)
        self.gp.fit(X, y, optimize=optimize)
        self._fitted = True

    def _random_sample(self) -> list[float]:
        return np.random.rand(self.dim).tolist()
