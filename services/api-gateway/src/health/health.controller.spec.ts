import { Test } from '@nestjs/testing';
import { TerminusModule } from '@nestjs/terminus';
import { HealthController } from './health.controller';

describe('HealthController', () => {
  it('reports app up', async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [TerminusModule],
      controllers: [HealthController],
    }).compile();
    const controller = moduleRef.get(HealthController);
    const result = await controller.liveness();
    expect(result.status).toBe('ok');
  });
});
