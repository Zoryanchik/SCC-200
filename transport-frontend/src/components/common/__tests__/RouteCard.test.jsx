import React from 'react';
import { render, screen } from '@testing-library/react';
import RouteCard from '../RouteCard';

describe('RouteCard', () => {
  test('renders step times with offsets when provided', () => {
    const route = {
      id: 1,
      duration: '1h 20m',
      transfers: 1,
      steps: [
        {
          type: 'walk',
          duration: '5 mins',
          to: 'Lancaster Station',
          departure_time_with_offset: null,
          arrival_time_with_offset: '10:02:00 (+0d)'
        },
        {
          type: 'bus',
          route: '40',
          duration: '30 mins',
          from: 'Lancaster',
          to: 'Preston',
          departure_time_with_offset: '10:05:00 (+0d)',
          arrival_time_with_offset: '10:35:00 (+0d)'
        }
      ],
    };

    render(<RouteCard route={route} />);

    // Expect both step time renderings to exist
    const firstStepTime = screen.queryByTestId('step-time-0');
    const secondStepTime = screen.queryByTestId('step-time-1');
    expect(firstStepTime).toBeTruthy();
    expect(firstStepTime.textContent).toContain('Arr: 10:02:00');
    expect(secondStepTime).toBeTruthy();
    expect(secondStepTime.textContent).toContain('Dep: 10:05:00');
    expect(secondStepTime.textContent).toContain('Arr: 10:35:00');
  });
});
