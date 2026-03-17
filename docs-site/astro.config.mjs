import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://docs.mithai.dev',
  output: 'static',
  integrations: [
    starlight({
      title: 'mithai',
      description: 'A framework for building AI agents for your organization.',
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
        replacesTitle: false,
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/nishantmodak/mithai' },
      ],
      editLink: {
        baseUrl: 'https://github.com/nishantmodak/mithai/edit/master/docs-site/src/content/docs/',
      },
      sidebar: [
        {
          label: 'Get started',
          items: [
            { label: 'Introduction', link: '/' },
            { label: 'Getting started', link: '/getting-started/' },
            { label: 'Your first skill', link: '/your-first-skill/' },
          ],
        },
        {
          label: 'Concepts',
          items: [
            { label: 'Core concepts', link: '/concepts/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Skills reference', link: '/skills-reference/' },
            { label: 'Configuration', link: '/configuration/' },
          ],
        },
        {
          label: 'Operations',
          items: [
            { label: 'Testing your skill', link: '/testing/' },
            { label: 'Deploy to production', link: '/deployment/' },
            { label: 'Security', link: '/security/' },
          ],
        },
        {
          label: 'Help',
          items: [
            { label: 'Troubleshooting', link: '/troubleshooting/' },
            { label: 'Examples', link: '/examples/' },
          ],
        },
      ],
      customCss: ['./src/styles/custom.css'],
    }),
  ],
});
