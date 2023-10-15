# SciRE-Solver: Accelerating Diffusion Models Sampling  by Score-integrand Solver with Recursive Difference



Created by [Shigui Li](https://ShiguiLi.github.io/)\*, [Wei Chen](https://scholar.google.com/citations?hl=en&user=n5VpiAMAAAAJ), [Delu Zeng](https://scholar.google.com/citations?user=08RCdoIAAAAJ)

This code is an official demo of PyTorch implementation of SciRE-Solver.

[ArXiv](https://doi.org/10.48550/arXiv.2308.07896)



# [Stable-Diffusion](https://github.com/Stability-AI/StableDiffusion) 
The code is now available in the ['sd_scire'](sd_scire/stable-diffusion/ldm/models/diffusion/scire_solver) folder, and we welcome everyone to use the scire-solver on stable-diffusion. Next, we will integrate the code into the stable-diffusion repository.

Here we provide two versions of the 'scire-solver', and three different types of methods.
- algorithm_type: 'scire_v1' or 'scire_v2'
- method: "multistep" or "singlestep"(agile) or "singlestep_fixed".

- When using a small number of sampling steps, we recommend using "multistep" method as:
  - "scire_v1-2m"
  - "scire_v1-3m"
  - "scire_v2-2m"
  - "scire_v2-3m" (at 6 steps, it is empirically better than other "m" options.)
- When the number of sampling steps '>= 26', we recommend trying all available options.

# Test SciRE-Solver on EDM (1.76 FID on CIFAR-10)
- SciRE_v1-2 (singlestep_fixed) with cpkt ([edm-cifar10-32x32-cond-vp.pkl](https://nvlabs-fi-cdn.nvidia.com/edm/pretrained/edm-cifar10-32x32-cond-vp.pkl)) attian $2.29$ FID with $12$ NFE, $2.16$ FID with $14$ NFE, $1.94$ FID with $20$ NFE, $1.79$ FID with $50$ NFE, $1.76$ FID with $100$ NFE, when $\phi_1(m)=\phi_1(3)$.

# TODO:

# Acknowledgement

Our code is based on [ScoreSDE](https://github.com/yang-song/score_sde) and [DPM-Solver](https://github.com/LuChengTHU/dpm-solver).

# Citation

If you find our work beneficial to you, please consider citing:

```
@article{li2023scireAD,
  title={SciRE-Solver: Accelerating Diffusion Models Sampling  by Score-integrand Solver with Recursive Difference},
  author={Li, Shigui and Chen, Wei and Zeng, Delu},
  journal={arXiv preprint arXiv:2308.07896},
  year={2023}
}
```
