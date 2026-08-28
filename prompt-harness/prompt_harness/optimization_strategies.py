"""可替换优化器策略（Sprint 3）。

为 Prompt 逆向推理提供多种优化策略，所有策略实现同一接口，
AutoSelector 自动选择最优策略。

策略列表：
  - GPStrategy (bo-gp-ei)   — 当前 GP + EI 贝叶斯优化
  - RandomStrategy (random)  — 随机搜索（baseline）
  - TPEStrategy (bo-tpe)     — Tree-structured Parzen Estimator
  - CMAESStrategy (cma-es)  — 协方差矩阵自适应进化策略（轻量实现）
  - EvolutionaryStrategy (evolutionary) — (μ+λ) 进化策略
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

import numpy as np
from scipy.stats import gaussian_kde, norm


# ---------------------------------------------------------------------------
# 抽象策略接口
# ---------------------------------------------------------------------------

class OptimizationStrategy(ABC):
    """优化器策略接口。

    所有策略必须实现 observe()、suggest()、best()、n_observations()。
    """

    def __init__(self, dim: int, bounds: list[tuple[float, float]] | None = None):
        self.dim = dim
        self.bounds = bounds or [(0.0, 1.0)] * dim

    @abstractmethod
    def observe(self, x: list[float], y: float) -> None:
        """添加观测点 (x, y)。"""

    @abstractmethod
    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        """建议下一个评估点。

        Returns:
            (x_next, info_dict)
        """

    @abstractmethod
    def best(self) -> tuple[list[float], float]:
        """返回当前最优 (x, y)。"""

    @abstractmethod
    def n_observations(self) -> int:
        """返回观测数。"""

    def get_convergence_curve(self) -> list[float]:
        """返回按观测顺序的 best-so-far 曲线。"""
        raise NotImplementedError

    def reset(self) -> None:
        """清空所有观测。"""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return type(self).__name__


# ---------------------------------------------------------------------------
# 策略 1：GP + EI（当前实现）
# ---------------------------------------------------------------------------

class GPStrategy(OptimizationStrategy):
    """GP + EI 贝叶斯优化。

    包装现有的 PromptBayesianOptimizer。
    """

    def __init__(
        self, dim: int, bounds: list[tuple[float, float]] | None = None,
        *, length_scale: float = 0.2, xi: float = 0.01, noise: float = 1e-6,
    ):
        super().__init__(dim, bounds)
        self.length_scale = length_scale
        self.xi = xi
        self.noise = noise

        # 使用现有的 PromptBayesianOptimizer
        from .bayesian_opt import PromptBayesianOptimizer
        self._opt = PromptBayesianOptimizer(dim=dim, bounds=bounds)

    def observe(self, x: list[float], y: float) -> None:
        self._opt.observe(x, y)

    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        return self._opt.suggest_next()

    def best(self) -> tuple[list[float], float]:
        return self._opt.get_best()

    def n_observations(self) -> int:
        return self._opt.n_observations()

    def get_convergence_curve(self) -> list[float]:
        return self._opt.get_convergence_curve()

    def reset(self) -> None:
        from .bayesian_opt import PromptBayesianOptimizer
        self._opt = PromptBayesianOptimizer(dim=self.dim, bounds=self.bounds)


# ---------------------------------------------------------------------------
# 策略 2：随机搜索（baseline）
# ---------------------------------------------------------------------------

class RandomStrategy(OptimizationStrategy):
    """纯随机搜索——作为 baseline，任何策略都应该比它好。"""

    def __init__(self, dim: int, bounds: list[tuple[float, float]] | None = None):
        super().__init__(dim, bounds)
        self._X: list[list[float]] = []
        self._y: list[float] = []

    def observe(self, x: list[float], y: float) -> None:
        self._X.append(list(x))
        self._y.append(float(y))

    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        x = [np.random.uniform(lo, hi) for lo, hi in self.bounds]
        return x, {"method": "random"}

    def best(self) -> tuple[list[float], float]:
        if not self._y:
            return [0.5] * self.dim, 0.0
        idx = int(np.argmax(self._y))
        return self._X[idx], self._y[idx]

    def n_observations(self) -> int:
        return len(self._y)

    def get_convergence_curve(self) -> list[float]:
        curve, best = [], -float("inf")
        for y in self._y:
            best = max(best, y)
            curve.append(best)
        return curve

    def reset(self) -> None:
        self._X.clear()
        self._y.clear()


# ---------------------------------------------------------------------------
# 策略 3：TPE（Tree-structured Parzen Estimator）
# ---------------------------------------------------------------------------

class TPEStrategy(OptimizationStrategy):
    """TPE——用 KDE 对好坏分布建模，通过似然比指导采样。

    与 GP 不同，TPE 不对目标函数直接建模，而是建模：
      ℓ(x) = P(x | y < γ)    — 差点的密度
      g(x) = P(x | y ≥ γ)    — 好点的密度
    建议点 argmax g(x) / ℓ(x)。
    """

    def __init__(
        self, dim: int, bounds: list[tuple[float, float]] | None = None,
        *, gamma: float = 0.25, bandwidth: float = 0.15,
    ):
        super().__init__(dim, bounds)
        self.gamma = gamma  # 分位数阈值（top-γ 视为"好"）
        self.bandwidth = bandwidth
        self._X: list[list[float]] = []
        self._y: list[float] = []
        self._best_y = -float("inf")

    def observe(self, x: list[float], y: float) -> None:
        self._X.append(list(x))
        self._y.append(float(y))
        self._best_y = max(self._best_y, float(y))

    def _split_by_threshold(self) -> tuple[np.ndarray, np.ndarray]:
        """将观测分为好/差两组。"""
        if len(self._y) < 3:
            return np.array(self._X), np.empty((0, self.dim))
        n_good = max(1, int(len(self._y) * self.gamma))
        sorted_indices = np.argsort(self._y)
        good_idx = sorted_indices[-n_good:]
        bad_idx = sorted_indices[:-n_good]
        X_arr = np.array(self._X)
        return X_arr[good_idx], X_arr[bad_idx]

    def _kde_density(self, x: np.ndarray, points: np.ndarray) -> float:
        """计算 x 在 points 分布下的密度（简化版）。"""
        if len(points) < 2:
            return 1.0
        try:
            # 用 1D 独立 KDE 近似（高维 KDE 不稳定且慢）
            log_density = 0.0
            for d in range(self.dim):
                vals = points[:, d]
                if vals.std() < 1e-6:
                    continue
                kde = gaussian_kde(vals, bw_method=self.bandwidth)
                log_density += kde.logpdf(x[d:d+1])[0]
            return math.exp(log_density / self.dim) if self.dim > 0 else 1.0
        except Exception:
            return 1.0

    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        n_obs = len(self._y)

        # 数据太少 → 随机探索
        if n_obs < 3:
            x = [np.random.uniform(lo, hi) for lo, hi in self.bounds]
            return x, {"method": "tpe_random_exploration"}

        good, bad = self._split_by_threshold()

        # 候选：从已有观测附近扰动 + 纯随机
        candidates = []
        for _ in range(200):
            if np.random.random() < 0.3 and len(self._X) > 0:
                # 从已有观测的邻域采样
                base = self._X[np.random.randint(len(self._X))]
                noise = np.random.normal(0, 0.12, size=self.dim)
                x = np.clip(np.array(base) + noise, 0.0, 1.0)
            else:
                x = np.random.uniform(0, 1, size=self.dim)
            candidates.append(x)

        # 评估每个候选：最大化 g(x) / ℓ(x)
        best_ratio = -float("inf")
        best_x = None
        for x in candidates:
            l_density = self._kde_density(x, bad)
            g_density = self._kde_density(x, good)
            ratio = g_density / (l_density + 1e-10)

            # 偶尔也选高随机候选（TPE 论文：有概率纯探索）
            if ratio > best_ratio:
                best_ratio = ratio
                best_x = x

        if best_x is None:
            best_x = np.random.uniform(0, 1, size=self.dim)

        return best_x.tolist(), {
            "method": "tpe",
            "ratio": round(float(best_ratio), 4),
            "n_good": len(good),
            "n_bad": len(bad),
        }

    def best(self) -> tuple[list[float], float]:
        if not self._y:
            return [0.5] * self.dim, 0.0
        idx = int(np.argmax(self._y))
        return self._X[idx], self._y[idx]

    def n_observations(self) -> int:
        return len(self._y)

    def get_convergence_curve(self) -> list[float]:
        curve, b = [], -float("inf")
        for y in self._y:
            b = max(b, y)
            curve.append(b)
        return curve

    def reset(self) -> None:
        self._X.clear()
        self._y.clear()
        self._best_y = -float("inf")


# ---------------------------------------------------------------------------
# 策略 4：轻量 CMA-ES（协方差矩阵自适应进化策略）
# ---------------------------------------------------------------------------

class CMAESStrategy(OptimizationStrategy):
    """轻量 CMA-ES 实现。

    用全协方差矩阵的自适应进化路径来优化。
    (μ, λ) 策略：每代选 μ 个父代生成 λ 个子代。
    """

    def __init__(
        self, dim: int, bounds: list[tuple[float, float]] | None = None,
        *, population_size: int | None = None, sigma: float = 0.3,
    ):
        super().__init__(dim, bounds)
        # CMA-ES 参数
        self.lambda_ = population_size or (4 + int(3 * math.log(dim)))
        self.sigma = sigma  # 初始步长
        self.mu = self.lambda_ // 2

        # 权重（用于加权重组）
        self.weights = [math.log(self.mu + 0.5) - math.log(i + 1) for i in range(self.mu)]
        self.weights = [w / sum(self.weights) for w in self.weights]
        self.mueff = 1.0 / sum(w ** 2 for w in self.weights)

        # 协方差矩阵自适应参数
        self.cc = (4.0 + self.mueff / self.dim) / (self.dim + 4.0 + 2.0 * self.mueff / self.dim)
        self.cs = (self.mueff + 2.0) / (self.dim + self.mueff + 5.0)
        self.c1 = 2.0 / ((self.dim + 1.3) ** 2 + self.mueff)
        self.cmu = min(1.0 - self.c1, 2.0 * (self.mueff - 2.0 + 1.0 / self.mueff) / ((self.dim + 2.0) ** 2 + self.mueff))
        self.damps = 1.0 + 2.0 * max(0.0, math.sqrt((self.mueff - 1.0) / (self.dim + 1.0)) - 1.0) + self.cs

        # 状态
        self.xmean = np.full(self.dim, 0.5)
        self.pc = np.zeros(self.dim)
        self.ps = np.zeros(self.dim)
        self.C = np.eye(self.dim)
        self.B = np.eye(self.dim)
        self.D = np.ones(self.dim)
        self._generation = 0
        self._all_X: list[list[float]] = []
        self._all_y: list[float] = []
        self._best_y = -float("inf")

    def observe(self, x: list[float], y: float) -> None:
        self._all_X.append(list(x))
        self._all_y.append(float(y))
        self._best_y = max(self._best_y, float(y))

    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        n_obs = len(self._all_y)
        if n_obs < self.lambda_:
            # 第一代：随机采样
            x = [np.random.uniform(lo, hi) for lo, hi in self.bounds]
            return x, {"method": "cmaes_init", "generation": 0}

        # 检查是否收集够了一代的观测
        if n_obs % self.lambda_ == 0 and self._generation > 0:
            # 更新分布
            self._update_distribution()

        # 采样新候选
        z = self.B @ (self.D * np.random.randn(self.dim))
        x_raw = self.xmean + self.sigma * z
        x = np.clip(np.array([x_raw]), 0.0, 1.0)[0]
        return x.tolist(), {
            "method": "cmaes_sample",
            "generation": self._generation,
            "sigma": round(self.sigma, 4),
        }

    def _update_distribution(self) -> None:
        """CMA-ES 核心更新：选择 → 重组 → 自适应。"""
        n = max(0, len(self._all_y) - self.lambda_)
        gen_y = self._all_y[n:]
        gen_X = self._all_X[n:]

        # 排序：y 降序
        sorted_idx = np.argsort(gen_y)[::-1]
        if len(sorted_idx) < self.mu:
            return

        # 加权重组
        xold = self.xmean.copy()
        self.xmean = np.zeros(self.dim)
        for i in range(self.mu):
            self.xmean += self.weights[i] * np.array(gen_X[sorted_idx[i]])
        zmean = np.linalg.solve(self.D * np.ones((self.dim, self.dim)) @ self.B.T,
                                 (self.xmean - xold).reshape(-1, 1)).ravel()

        # 更新进化路径
        self.ps = (1.0 - self.cs) * self.ps + math.sqrt(self.cs * (2.0 - self.cs) * self.mueff) * zmean
        hsig = (np.linalg.norm(self.ps) / math.sqrt(1.0 - (1.0 - self.cs) ** (2 * n / self.lambda_))
                < (1.4 + 2.0 / (self.dim + 1.0)) * math.sqrt(self.dim))
        self.pc = (1.0 - self.cc) * self.pc + hsig * math.sqrt(self.cc * (2.0 - self.cc) * self.mueff) * (self.xmean - xold) / self.sigma

        # 更新协方差矩阵
        artmp = (np.array([gen_X[sorted_idx[i]] for i in range(self.mu)]) - xold) / self.sigma
        self.C = ((1.0 - self.c1 - self.cmu) * self.C
                  + self.c1 * (np.outer(self.pc, self.pc)
                               + (1.0 - hsig) * self.cc * (2.0 - self.cc) * self.C)
                  + self.cmu * sum(self.weights[i] * np.outer(artmp[i], artmp[i]) for i in range(self.mu)))

        # 更新步长
        self.sigma *= math.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / math.sqrt(self.dim) - 1.0))
        self.sigma = max(0.01, min(1.0, self.sigma))

        # 特征分解
        try:
            evals, evect = np.linalg.eigh(self.C)
            evals = np.maximum(evals, 1e-10)
            self.D = np.sqrt(evals)
            self.B = evect
        except np.linalg.LinAlgError:
            pass

        self._generation += 1

    def best(self) -> tuple[list[float], float]:
        if not self._all_y:
            return [0.5] * self.dim, 0.0
        idx = int(np.argmax(self._all_y))
        return self._all_X[idx], self._all_y[idx]

    def n_observations(self) -> int:
        return len(self._all_y)

    def get_convergence_curve(self) -> list[float]:
        curve, b = [], -float("inf")
        for y in self._all_y:
            b = max(b, y)
            curve.append(b)
        return curve

    def reset(self) -> None:
        self.xmean = np.full(self.dim, 0.5)
        self.pc = np.zeros(self.dim)
        self.ps = np.zeros(self.dim)
        self.C = np.eye(self.dim)
        self.B = np.eye(self.dim)
        self.D = np.ones(self.dim)
        self._generation = 0
        self._all_X.clear()
        self._all_y.clear()
        self._best_y = -float("inf")


# ---------------------------------------------------------------------------
# 策略 5：(μ+λ) 进化策略
# ---------------------------------------------------------------------------

class EvolutionaryStrategy(OptimizationStrategy):
    """(μ+λ) 进化策略。

    每代：
    - 从父代选 μ 个最优
    - λ 个子代通过交叉 + 变异生成
    - (μ+λ) 选择：父代 + 子代中选最优的 μ 个进入下一代
    """

    def __init__(
        self, dim: int, bounds: list[tuple[float, float]] | None = None,
        *, population_size: int = 15, mutation_rate: float = 0.15,
        crossover_rate: float = 0.5,
    ):
        super().__init__(dim, bounds)
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self._mu = max(3, population_size // 3)
        self._all_X: list[list[float]] = []
        self._all_y: list[float] = []
        self._best_y = -float("inf")

    def observe(self, x: list[float], y: float) -> None:
        self._all_X.append(list(x))
        self._all_y.append(float(y))
        self._best_y = max(self._best_y, float(y))

    def suggest(self) -> tuple[list[float], dict[str, Any]]:
        n_obs = len(self._all_y)
        if n_obs < self.population_size:
            x = [np.random.uniform(lo, hi) for lo, hi in self.bounds]
            return x, {"method": "evo_init"}

        # 选出当前最优的 μ 个作为父代
        sorted_idx = np.argsort(self._all_y)[::-1]
        parents_idx = sorted_idx[:self._mu]

        # 选择繁殖方式
        if np.random.random() < self.crossover_rate and len(parents_idx) >= 2:
            # 交叉：从两个父代中随机交叉
            p1 = np.array(self._all_X[np.random.choice(parents_idx)])
            p2 = np.array(self._all_X[np.random.choice(parents_idx)])
            mask = np.random.random(self.dim) < 0.5
            child = np.where(mask, p1, p2)
        else:
            # 变异：从单个父代扰动
            parent = np.array(self._all_X[np.random.choice(parents_idx)])
            child = parent.copy()

        # 高斯变异
        mutation = np.random.normal(0, self.mutation_rate, size=self.dim)
        child = child + mutation
        child = np.clip(child, 0.0, 1.0)

        return child.tolist(), {
            "method": "evolutionary",
            "n_obs": n_obs,
            "best_so_far": round(float(self._best_y), 4),
        }

    def best(self) -> tuple[list[float], float]:
        if not self._all_y:
            return [0.5] * self.dim, 0.0
        idx = int(np.argmax(self._all_y))
        return self._all_X[idx], self._all_y[idx]

    def n_observations(self) -> int:
        return len(self._all_y)

    def get_convergence_curve(self) -> list[float]:
        curve, b = [], -float("inf")
        for y in self._all_y:
            b = max(b, y)
            curve.append(b)
        return curve

    def reset(self) -> None:
        self._all_X.clear()
        self._all_y.clear()
        self._best_y = -float("inf")


# ---------------------------------------------------------------------------
# 策略工厂
# ---------------------------------------------------------------------------

# 注册表：名称 → 构造器
_STRATEGY_REGISTRY: dict[str, type[OptimizationStrategy]] = {
    "bo-gp-ei": GPStrategy,
    "random": RandomStrategy,
    "bo-tpe": TPEStrategy,
    "cma-es": CMAESStrategy,
    "evolutionary": EvolutionaryStrategy,
}


def create_strategy(
    name: str,
    dim: int,
    bounds: list[tuple[float, float]] | None = None,
    **kwargs,
) -> OptimizationStrategy:
    """按名称创建优化策略实例。

    Args:
        name: 策略名称（"bo-gp-ei" / "random" / "bo-tpe" / "cma-es" / "evolutionary"）
        dim: 搜索空间维度
        bounds: 边界
        **kwargs: 传递给策略构造器的额外参数

    Returns:
        OptimizationStrategy 实例
    """
    cls = _STRATEGY_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(_STRATEGY_REGISTRY)
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return cls(dim=dim, bounds=bounds, **kwargs)


def list_strategies() -> list[str]:
    """返回所有可用策略名称列表。"""
    return list(_STRATEGY_REGISTRY.keys())


# ---------------------------------------------------------------------------
# AutoSelector：自动策略选择
# ---------------------------------------------------------------------------

class AutoSelector:
    """自动选择最优优化策略。

    工作机制：
    1. 从候选策略池中选 2-3 个
    2. 给每个策略相同的初始预算（总迭代的 30%）
    3. 比较各策略在同等预算下的 best_score
    4. 胜出策略获得剩余 70% 预算继续优化
    5. 记录策略选择结果

    用法：
        selector = AutoSelector(dim=40)   # dim = P_DIM_META["dim"]
        # 先收集初始观测（KNN + LHS）
        for p, s in initial:
            selector.observe_shared(p, s)  # 所有策略共享初始点
        # 初始阶段结束后，选择候选策略
        selector.select_candidates(n_candidates=2)
        # 分配评估
        for _ in range(budget):
            name = selector.next_strategy()
            p_next = selector.suggest(name)
            ...
    """

    def __init__(
        self,
        dim: int,
        bounds: list[tuple[float, float]] | None = None,
        strategy_pool: list[str] | None = None,
    ):
        self.dim = dim
        self.bounds = bounds
        self.strategy_pool = strategy_pool or ["bo-gp-ei", "bo-tpe", "evolutionary"]

        # 所有策略实例
        self.strategies: dict[str, OptimizationStrategy] = {}
        for name in self.strategy_pool:
            self.strategies[name] = create_strategy(name, dim, bounds)

        # 共享观测（初始点 => 所有策略共享）
        self._shared_obs: list[tuple[list[float], float]] = []

        # 竞选状态
        self._candidates: list[str] = []  # 当前竞选的策略名
        self._strategy_budget: int = 0   # 每个策略的竞选预算
        self._strategy_usage: dict[str, int] = {}  # 各策略已用预算
        self._champion: str | None = None  # 胜出策略
        self._phase: str = "shared"  # shared / selection / champion
        self._total_used: int = 0
        self._all_names: list[str] = []  # 按顺序记录使用的策略名

    def observe_shared(self, x: list[float], y: float) -> None:
        """添加共享观测（所有策略共享的初始点）。"""
        self._shared_obs.append((list(x), float(y)))
        for s in self.strategies.values():
            s.observe(x, y)

    def select_candidates(self, n_candidates: int = 2, selection_budget_ratio: float = 0.3) -> None:
        """选择候选策略并分配竞选预算。

        Args:
            n_candidates: 参加竞选的策略数
            selection_budget_ratio: 竞选阶段占总预算的比例
        """
        # 从策略池中随机选 n_candidates 个
        pool = list(self.strategies.keys())
        if len(pool) <= n_candidates:
            self._candidates = pool
        else:
            # 优先选未试过的；如果都用过，选上次胜出的 + 随机
            self._candidates = list(np.random.choice(pool, size=n_candidates, replace=False))

        self._total_budget = sum(
            s.n_observations() for s in self.strategies.values()
        )
        self._phase = "selection"

    def set_budget(self, total_iterations: int, selection_budget_ratio: float = 0.3) -> None:
        """设置总预算并分配竞选预算。在 select_candidates 之后调用。"""
        n_candidates = len(self._candidates)
        if n_candidates == 0:
            self._phase = "champion"
            return
        per_strategy = max(1, int(total_iterations * selection_budget_ratio / n_candidates))
        self._strategy_budget = per_strategy
        self._strategy_usage = {name: 0 for name in self._candidates}
        self._selection_total = per_strategy * n_candidates
        self._phase = "selection"

    def next_strategy(self) -> str | None:
        """返回当前应使用的策略名称。

        Returns:
            策略名，如果没有策略可用（所有策略用尽）则返回 None
        """
        self._total_used += 1

        if self._phase == "shared":
            # 所有策略相同，随机选一个
            name = np.random.choice(list(self.strategies.keys()))
            self._all_names.append(name)
            return name

        if self._phase == "selection":
            # 竞选阶段：轮询
            for name in self._candidates:
                usage = self._strategy_usage.get(name, 0)
                if usage < self._strategy_budget:
                    self._strategy_usage[name] = usage + 1
                    self._all_names.append(name)
                    return name
            # 所有竞选策略都用完了 → 进入冠军阶段
            self._select_champion()
            return self._pick_champion()

        if self._phase == "champion":
            return self._pick_champion()

        return None

    def _select_champion(self) -> None:
        """选择冠军策略。"""
        if not self._candidates:
            self._champion = list(self.strategies.keys())[0]
            return

        # 比较各策略在竞选阶段的 best_score
        best_y = -float("inf")
        best_name = self._candidates[0]
        for name in self._candidates:
            _, y = self.strategies[name].best()
            if y > best_y:
                best_y = y
                best_name = name
        self._champion = best_name
        self._phase = "champion"

    def _pick_champion(self) -> str | None:
        """返回冠军策略。"""
        if self._champion:
            self._all_names.append(self._champion)
            return self._champion
        return None

    def get_champion_name(self) -> str | None:
        return self._champion

    def get_strategy(self, name: str) -> OptimizationStrategy:
        return self.strategies[name]

    def get_summary(self) -> dict[str, Any]:
        return {
            "phase": self._phase,
            "candidates": self._candidates,
            "champion": self._champion,
            "all_strategies_used": self._all_names,
            "strategy_usage": self._strategy_usage,
            "n_shared_obs": len(self._shared_obs),
        }

    def reset(self) -> None:
        for s in self.strategies.values():
            s.reset()
        self._shared_obs.clear()
        self._candidates.clear()
        self._strategy_usage.clear()
        self._champion = None
        self._phase = "shared"
        self._total_used = 0
        self._all_names.clear()
